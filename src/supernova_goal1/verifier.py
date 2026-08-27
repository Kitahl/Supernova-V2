from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
import os
from os import PathLike
from pathlib import Path
import signal
import subprocess
from time import monotonic
from typing import Mapping, Sequence


class VerifierStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


# Descriptive alias for callers that prefer the noun used by the ticket.
VerificationStatus = VerifierStatus


@dataclass(frozen=True)
class VerifierResult:
    status: VerifierStatus
    command: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    elapsed_milliseconds: int
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.status is VerifierStatus.PASS


def _normalize_command(command: Sequence[str]) -> tuple[str, ...]:
    if isinstance(command, (str, bytes)):
        raise TypeError("command must be a sequence of argument strings, not a shell string")
    normalized = tuple(command)
    if not normalized:
        raise ValueError("command must contain at least one argument")
    if not all(isinstance(argument, str) for argument in normalized):
        raise TypeError("command arguments must all be strings")
    if not normalized[0]:
        raise ValueError("command executable must be a non-empty string")
    return normalized


def _normalize_timeout(timeout_seconds: float) -> float:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise TypeError("timeout_seconds must be a finite positive number")
    timeout = float(timeout_seconds)
    if not isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_seconds must be a finite positive number")
    return timeout


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _popen_isolation() -> dict[str, object]:
    """Return platform isolation flags used to contain verifier descendants."""

    if os.name == "posix":
        # A new session gives the verifier a process group whose descendants can
        # be killed atomically when the verifier exceeds its budget.
        return {"start_new_session": True}
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    raise RuntimeError(f"verifier process-tree containment unsupported on os.name={os.name!r}")


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    """Terminate the verifier and descendants created inside its isolated group."""

    if process.poll() is not None:
        return

    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return

    if os.name == "nt":
        # Windows does not expose POSIX-style killpg. taskkill /T applies the
        # termination to the process and its descendant tree.
        cleanup = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            timeout=5,
            check=False,
        )
        if cleanup.returncode not in (0, 128):
            raise RuntimeError(f"taskkill failed with returncode={cleanup.returncode}")
        if process.poll() is None:
            process.kill()
        return

    raise RuntimeError(f"verifier process-tree containment unsupported on os.name={os.name!r}")


def run_verifier(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    cwd: str | PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> VerifierResult:
    """Run one external verifier under a process-tree-contained hard timeout.

    Exit code zero is PASS, a completed non-zero exit is FAIL, expiration of the
    timeout is TIMEOUT only after the isolated verifier process tree is terminated,
    and process-start/runtime/containment failures are ERROR. The command is always
    executed directly with ``shell=False``.
    """

    normalized_command = _normalize_command(command)
    timeout = _normalize_timeout(timeout_seconds)
    normalized_cwd = None if cwd is None else str(Path(cwd))
    started = monotonic()

    try:
        isolation = _popen_isolation()
        process = subprocess.Popen(
            normalized_command,
            cwd=normalized_cwd,
            env=None if env is None else dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            **isolation,
        )
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        elapsed_ms = max(0, round((monotonic() - started) * 1000))
        return VerifierResult(
            status=VerifierStatus.ERROR,
            command=normalized_command,
            returncode=None,
            stdout="",
            stderr="",
            elapsed_milliseconds=elapsed_ms,
            error=f"{type(exc).__name__}: {exc}",
        )

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            _kill_process_tree(process)
            stdout, stderr = process.communicate(timeout=5)
        except (OSError, subprocess.SubprocessError, RuntimeError) as cleanup_exc:
            if process.poll() is None:
                process.kill()
                process.communicate()
            elapsed_ms = max(0, round((monotonic() - started) * 1000))
            return VerifierResult(
                status=VerifierStatus.ERROR,
                command=normalized_command,
                returncode=None,
                stdout=_text(exc.stdout),
                stderr=_text(exc.stderr),
                elapsed_milliseconds=elapsed_ms,
                error=(
                    "verifier exceeded timeout but process-tree containment failed: "
                    f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                ),
            )

        elapsed_ms = max(0, round((monotonic() - started) * 1000))
        return VerifierResult(
            status=VerifierStatus.TIMEOUT,
            command=normalized_command,
            returncode=None,
            stdout=_text(stdout),
            stderr=_text(stderr),
            elapsed_milliseconds=elapsed_ms,
            error=f"verifier exceeded timeout_seconds={timeout:g}; process tree terminated",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if process.poll() is None:
            try:
                _kill_process_tree(process)
            finally:
                process.communicate()
        elapsed_ms = max(0, round((monotonic() - started) * 1000))
        return VerifierResult(
            status=VerifierStatus.ERROR,
            command=normalized_command,
            returncode=None,
            stdout="",
            stderr="",
            elapsed_milliseconds=elapsed_ms,
            error=f"{type(exc).__name__}: {exc}",
        )

    elapsed_ms = max(0, round((monotonic() - started) * 1000))
    status = VerifierStatus.PASS if process.returncode == 0 else VerifierStatus.FAIL
    return VerifierResult(
        status=status,
        command=normalized_command,
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
        elapsed_milliseconds=elapsed_ms,
    )


__all__ = [
    "VerificationStatus",
    "VerifierResult",
    "VerifierStatus",
    "run_verifier",
]
