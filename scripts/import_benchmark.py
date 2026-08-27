from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
HASH_ALGORITHM = "sha256"
DEFAULT_LOCK_PATH = Path("goal1/BENCHMARK.lock.json")
_PATH_IS_JUNCTION = getattr(Path, "is_junction", None)
_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x00000400)
_WINDOWS_IO_REPARSE_TAG_MOUNT_POINT = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"benchmark lock contains duplicate JSON object member: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"benchmark lock contains non-standard JSON constant: {value}")


def _require_text(label: str, value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{label} must be non-empty")
    return value


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _is_junction(path: Path) -> bool:
    if _PATH_IS_JUNCTION is not None:
        return bool(_PATH_IS_JUNCTION(path))
    if os.name != "nt":
        return False

    path_stat = path.lstat()
    return bool(
        getattr(path_stat, "st_file_attributes", 0) & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT
        and getattr(path_stat, "st_reparse_tag", 0) == _WINDOWS_IO_REPARSE_TAG_MOUNT_POINT
    )


def _sha256_file(path: Path) -> tuple[str, int, tuple[int, int, int, int]]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"benchmark tree contains non-regular file: {path}")

    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError(f"benchmark file changed while opening for hashing: {path}")
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
        after_read = os.fstat(handle.fileno())

    after_path = path.lstat()
    expected = _stat_signature(before)
    if (
        _stat_signature(opened) != expected
        or _stat_signature(after_read) != expected
        or _stat_signature(after_path) != expected
        or not stat.S_ISREG(after_path.st_mode)
        or size != after_read.st_size
    ):
        raise ValueError(f"benchmark file changed while hashing: {path}")
    return digest.hexdigest(), size, expected


def _walk_snapshot(
    root: Path,
    *,
    hash_files: bool,
) -> tuple[list[dict[str, Any]], dict[str, tuple[int, int, int, int]], dict[str, tuple[int, int, int, int]]]:
    entries: list[dict[str, Any]] = []
    file_signatures: dict[str, tuple[int, int, int, int]] = {}
    directory_signatures: dict[str, tuple[int, int, int, int]] = {}

    def raise_walk_error(error: OSError) -> None:
        raise error

    for current, dirnames, filenames in os.walk(
        root,
        topdown=True,
        onerror=raise_walk_error,
        followlinks=False,
    ):
        current_path = Path(current)
        current_stat = current_path.lstat()
        current_relative = "." if current_path == root else current_path.relative_to(root).as_posix()
        if current_path.is_symlink() or _is_junction(current_path) or not stat.S_ISDIR(current_stat.st_mode):
            raise ValueError(f"benchmark tree contains non-directory path during traversal: {current_relative}")
        directory_signatures[current_relative] = _stat_signature(current_stat)

        dirnames.sort()
        filenames.sort()

        for dirname in dirnames:
            candidate = current_path / dirname
            if candidate.is_symlink():
                raise ValueError(
                    f"benchmark tree contains symlinked directory: {candidate.relative_to(root).as_posix()}"
                )
            if _is_junction(candidate):
                raise ValueError(
                    f"benchmark tree contains junction directory: {candidate.relative_to(root).as_posix()}"
                )

        for filename in filenames:
            candidate = current_path / filename
            relative = candidate.relative_to(root).as_posix()
            if candidate.is_symlink():
                raise ValueError(f"benchmark tree contains symlinked file: {relative}")
            candidate_stat = candidate.lstat()
            if not stat.S_ISREG(candidate_stat.st_mode):
                raise ValueError(f"benchmark tree contains non-regular file: {relative}")

            if hash_files:
                digest, size, signature = _sha256_file(candidate)
                entries.append({"path": relative, "sha256": digest, "bytes": size})
            else:
                signature = _stat_signature(candidate_stat)
            file_signatures[relative] = signature

    entries.sort(key=lambda entry: entry["path"])
    return entries, file_signatures, directory_signatures


def _validate_snapshot(
    root: Path,
    file_signatures: dict[str, tuple[int, int, int, int]],
    directory_signatures: dict[str, tuple[int, int, int, int]],
) -> None:
    _, current_files, current_directories = _walk_snapshot(root, hash_files=False)
    if current_files != file_signatures or current_directories != directory_signatures:
        raise ValueError("benchmark tree changed while hashing: paths or metadata changed during traversal")

    for relative, expected in file_signatures.items():
        current = (root / relative).lstat()
        if not stat.S_ISREG(current.st_mode) or _stat_signature(current) != expected:
            raise ValueError(f"benchmark tree changed while hashing: file changed after traversal: {relative}")

    for relative, expected in directory_signatures.items():
        directory = root if relative == "." else root / relative
        current = directory.lstat()
        if (
            directory.is_symlink()
            or _is_junction(directory)
            or not stat.S_ISDIR(current.st_mode)
            or _stat_signature(current) != expected
        ):
            raise ValueError(f"benchmark tree changed while hashing: directory changed after traversal: {relative}")


def _enumerate_files(root: Path) -> list[dict[str, Any]]:
    entries, file_signatures, directory_signatures = _walk_snapshot(root, hash_files=True)
    _validate_snapshot(root, file_signatures, directory_signatures)
    return entries


def build_lock(source: Path, *, name: str, version: str, split: str) -> dict[str, Any]:
    if source.is_symlink():
        raise ValueError(f"benchmark source must not be a symlink: {source}")
    if _is_junction(source):
        raise ValueError(f"benchmark source must not be a junction: {source}")
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
    temporary: Path | None = None
    descriptor = -1
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            text=True,
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            descriptor = -1
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _load_lock(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_strict_json_object,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(value, dict):
        raise ValueError("benchmark lock must contain a JSON object")
    return value


def _require_control_path_outside_source(source: Path, control_path: Path, *, label: str) -> None:
    source = source.resolve()
    control_path = control_path.resolve()
    try:
        control_path.relative_to(source)
    except ValueError:
        return
    raise ValueError(f"{label} must be outside benchmark source: {control_path}")


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
            _require_control_path_outside_source(args.source, args.output, label="benchmark lock output")
            lock = build_lock(args.source, name=args.name, version=args.version, split=args.split)
            _write_lock(args.output, lock)
            print(_summary("lock", lock, status="LOCKED"))
            return 0

        _require_control_path_outside_source(args.source, args.lock, label="benchmark lock file")
        lock = _load_lock(args.lock)
        verified = verify_lock(args.source, lock)
        print(_summary("check", verified, status="PASS"))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
