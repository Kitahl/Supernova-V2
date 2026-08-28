from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parent
EXPECTED_LEAN_VERSION = "4.33.1"
VERSION_TIMEOUT_SECONDS = 60
SMOKE_TIMEOUT_SECONDS = 180


def _run(command: Sequence[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def check_runtime() -> dict[str, object]:
    version = _run(("lake", "env", "lean", "--version"), timeout=VERSION_TIMEOUT_SECONDS)
    if version.returncode != 0:
        raise RuntimeError(f"Lean version command failed: {version.stderr.strip()}")
    first_line = version.stdout.splitlines()[0] if version.stdout.splitlines() else ""
    expected_marker = f"Lean (version {EXPECTED_LEAN_VERSION},"
    if expected_marker not in first_line:
        raise RuntimeError(
            f"wrong Lean runtime: expected marker {expected_marker!r}, observed {first_line!r}"
        )

    smoke = _run(
        ("lake", "env", "lean", "SupernovaGoal1Smoke.lean"),
        timeout=SMOKE_TIMEOUT_SECONDS,
    )
    if smoke.returncode != 0:
        raise RuntimeError(f"Lean smoke compile failed: {smoke.stderr.strip()}")
    return {
        "status": "PASS",
        "lean_version": EXPECTED_LEAN_VERSION,
        "version_output": first_line,
        "smoke_file": "SupernovaGoal1Smoke.lean",
    }


def main() -> int:
    try:
        print(json.dumps(check_runtime(), sort_keys=True))
        return 0
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
