from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
HASH_ALGORITHM = "sha256"
DEFAULT_LOCK_PATH = Path("goal1/BENCHMARK.lock.json")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _require_text(label: str, value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{label} must be non-empty")
    return value


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _enumerate_files(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    def raise_walk_error(error: OSError) -> None:
        raise error

    for current, dirnames, filenames in os.walk(
        root,
        topdown=True,
        onerror=raise_walk_error,
        followlinks=False,
    ):
        current_path = Path(current)
        dirnames.sort()
        filenames.sort()

        for dirname in dirnames:
            candidate = current_path / dirname
            if candidate.is_symlink():
                raise ValueError(
                    f"benchmark tree contains symlinked directory: {candidate.relative_to(root).as_posix()}"
                )

        for filename in filenames:
            candidate = current_path / filename
            relative = candidate.relative_to(root).as_posix()
            if candidate.is_symlink():
                raise ValueError(f"benchmark tree contains symlinked file: {relative}")
            if not candidate.is_file():
                raise ValueError(f"benchmark tree contains non-regular file: {relative}")
            digest, size = _sha256_file(candidate)
            entries.append({"path": relative, "sha256": digest, "bytes": size})

    entries.sort(key=lambda entry: entry["path"])
    return entries


def build_lock(source: Path, *, name: str, version: str, split: str) -> dict[str, Any]:
    if source.is_symlink():
        raise ValueError(f"benchmark source must not be a symlink: {source}")
    source = source.resolve()
    if not source.is_dir():
        raise ValueError(f"benchmark source is not a directory: {source}")

    identity = {
        "name": _require_text("name", name),
        "version": _require_text("version", version),
        "split": _require_text("split", split),
    }
    files = _enumerate_files(source)
    if not files:
        raise ValueError("benchmark source contains no regular files")

    digest_payload = {
        "schema_version": SCHEMA_VERSION,
        "benchmark": identity,
        "files": files,
    }
    root_sha256 = hashlib.sha256(_canonical_json(digest_payload)).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "LOCKED",
        "benchmark": identity,
        "content": {
            "algorithm": HASH_ALGORITHM,
            "root_sha256": root_sha256,
            "file_count": len(files),
            "total_bytes": sum(entry["bytes"] for entry in files),
            "files": files,
        },
    }


def verify_lock(source: Path, lock: dict[str, Any]) -> dict[str, Any]:
    if lock.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported benchmark lock schema_version")
    if lock.get("status") != "LOCKED":
        raise ValueError("benchmark lock is not in LOCKED state")

    benchmark = lock.get("benchmark")
    if not isinstance(benchmark, dict):
        raise ValueError("benchmark lock is missing benchmark identity")
    try:
        expected = build_lock(
            source,
            name=str(benchmark["name"]),
            version=str(benchmark["version"]),
            split=str(benchmark["split"]),
        )
    except KeyError as exc:
        raise ValueError(f"benchmark lock identity missing field: {exc.args[0]}") from exc

    if expected != lock:
        expected_root = expected["content"]["root_sha256"]
        observed_root = lock.get("content", {}).get("root_sha256") if isinstance(lock.get("content"), dict) else None
        raise ValueError(
            f"benchmark content does not match lock: expected root {observed_root}, observed {expected_root}"
        )
    return expected


def _write_lock(path: Path, lock: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(lock, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(path)


def _load_lock(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("benchmark lock must contain a JSON object")
    return value


def _summary(command: str, lock: dict[str, Any], *, status: str) -> str:
    content = lock["content"]
    return json.dumps(
        {
            "command": command,
            "status": status,
            "root_sha256": content["root_sha256"],
            "file_count": content["file_count"],
            "total_bytes": content["total_bytes"],
        },
        sort_keys=True,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or verify a deterministic content lock for benchmark bytes without copying benchmark data."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    lock_parser = subparsers.add_parser("lock", help="hash a local benchmark tree and write a lock file")
    lock_parser.add_argument("source", type=Path)
    lock_parser.add_argument("--name", required=True)
    lock_parser.add_argument("--version", required=True)
    lock_parser.add_argument("--split", required=True)
    lock_parser.add_argument("--output", type=Path, default=DEFAULT_LOCK_PATH)

    check_parser = subparsers.add_parser("check", help="verify local benchmark bytes against an existing lock")
    check_parser.add_argument("source", type=Path)
    check_parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK_PATH)
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv[1:])
    try:
        if args.command == "lock":
            lock = build_lock(args.source, name=args.name, version=args.version, split=args.split)
            _write_lock(args.output, lock)
            print(_summary("lock", lock, status="LOCKED"))
            return 0

        lock = _load_lock(args.lock)
        verified = verify_lock(args.source, lock)
        print(_summary("check", verified, status="PASS"))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
