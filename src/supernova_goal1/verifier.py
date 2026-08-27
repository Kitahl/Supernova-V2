from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from os import PathLike
from pathlib import Path
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


def run_verifier(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    cwd: str | PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> VerifierResult:
    """Run one external verifier under a hard caller-supplied timeout.

    Exit code zero is PASS, a completed non-zero exit is FAIL, expiration of the
    timeout is TIMEOUT, and process-start/runtime failures are ERROR. The command
    is always executed directly with ``shell=False``.
    """

    normalized_command = _normalize_command(command)
    timeout = _normalize_timeout(timeout_seconds)
    normalized_cwd = None if cwd is None else str(Path(cwd))
    started = monotonic()

    try:
        completed = subprocess.run(
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
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = max(0, round((monotonic() - started) * 1000))
        return VerifierResult(
            status=VerifierStatus.TIMEOUT,
            command=normalized_command,
            returncode=None,
            stdout=_text(exc.stdout),
            stderr=_text(exc.stderr),
            elapsed_milliseconds=elapsed_ms,
            error=f"verifier exceeded timeout_seconds={timeout:g}",
        )
    except (OSError, subprocess.SubprocessError) as exc:
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
    status = VerifierStatus.PASS if completed.returncode == 0 else VerifierStatus.FAIL
    return VerifierResult(
        status=status,
        command=normalized_command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        elapsed_milliseconds=elapsed_ms,
    )


__all__ = [
    "VerificationStatus",
    "VerifierResult",
    "VerifierStatus",
    "run_verifier",
]
