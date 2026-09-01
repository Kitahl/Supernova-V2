from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

SCHEMA = "supernova.goal1.verifier-runtime-lock.v1"
ENVIRONMENT_KEYS = (
    "LEAN_PATH",
    "LEAN_SRC_PATH",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
)
PERMITTED_AXIOMS = ("propext", "Quot.sound", "Classical.choice")
PRIMITIVE_TARGETS = (
    "Nat",
    "String",
    "String.mk",
    "Char",
    "Char.ofNat",
    "List",
    "Quot",
    "Quot.mk",
    "Quot.lift",
    "Quot.ind",
    "Nat.add",
    "Nat.sub",
    "Nat.mul",
    "Nat.pow",
    "Nat.gcd",
    "Nat.div",
    "Nat.mod",
    "Nat.beq",
    "Nat.ble",
    "Nat.land",
    "Nat.lor",
    "Nat.xor",
    "Nat.shiftLeft",
    "Nat.shiftRight",
    "String.ofList",
)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command_stdout(command: list[str]) -> str:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def normalized_path(
    path: Path,
    *,
    build_sysroot: Path,
    runtime_sysroot: Path,
) -> str:
    value = path.as_posix()
    build = build_sysroot.as_posix().rstrip("/")
    if value == build or value.startswith(build + "/"):
        return runtime_sysroot.as_posix().rstrip("/") + value[len(build) :]
    return value


def normalized_environment(
    *,
    build_sysroot: Path,
    runtime_sysroot: Path,
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    build = build_sysroot.as_posix().rstrip("/")
    runtime = runtime_sysroot.as_posix().rstrip("/")
    for key in ENVIRONMENT_KEYS:
        raw = os.environ.get(key, "")
        entries = []
        for entry in raw.split(os.pathsep):
            if not entry:
                continue
            value = entry.replace(build, runtime)
            if build in value:
                raise RuntimeError(f"{key} retains the build sysroot")
            entries.append(value)
        result[key] = entries
    return result


def dependency_graph(template: Path) -> list[dict[str, object]]:
    manifest_path = template / "lake-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    packages = manifest.get("packages")
    if not isinstance(packages, list):
        raise TypeError("lake-manifest.json packages changed")
    frozen: list[dict[str, object]] = []
    for package in packages:
        if not isinstance(package, dict):
            raise TypeError("lake-manifest.json package changed")
        frozen.append(
            {
                key: package.get(key)
                for key in (
                    "name",
                    "scope",
                    "url",
                    "rev",
                    "inputRev",
                    "subDir",
                    "configFile",
                )
            }
        )
    return sorted(frozen, key=lambda item: (str(item["scope"]), str(item["name"])))


def artifact(path: Path, runtime_path: str) -> dict[str, object]:
    if not path.is_file():
        raise RuntimeError(f"trusted artifact is missing: {path}")
    return {
        "path": runtime_path,
        "sha256": file_sha256(path),
        "size": path.stat().st_size,
    }


def build_lock(args: argparse.Namespace) -> dict[str, object]:
    template = args.template.resolve()
    build_sysroot = args.build_sysroot.resolve()
    runtime_sysroot = Path(args.runtime_sysroot)
    lean = build_sysroot / "bin" / "lean"

    trusted_oleans = []
    for path in sorted(template.rglob("*.olean"), key=lambda item: item.as_posix()):
        trusted_oleans.append(
            artifact(
                path,
                normalized_path(
                    path,
                    build_sysroot=build_sysroot,
                    runtime_sysroot=runtime_sysroot,
                ),
            )
        )
    if not trusted_oleans:
        raise RuntimeError("no trusted .olean files were materialized")

    binaries: dict[str, dict[str, object]] = {}
    for name, build_path, runtime_path in (
        ("lean", lean, runtime_sysroot / "bin" / "lean"),
        (
            "lean4export",
            Path("/opt/supernova/bin/lean4export"),
            Path("/opt/supernova/bin/lean4export"),
        ),
        (
            "check_exports",
            Path("/opt/supernova/bin/check_exports"),
            Path("/opt/supernova/bin/check_exports"),
        ),
        (
            "nanoda",
            Path("/opt/supernova/bin/nanoda_bin"),
            Path("/opt/supernova/bin/nanoda_bin"),
        ),
        (
            "parse_product",
            Path("/opt/supernova/bin/parse_product"),
            Path("/opt/supernova/bin/parse_product"),
        ),
        (
            "entrypoint",
            Path("/opt/supernova/entrypoint.py"),
            Path("/opt/supernova/entrypoint.py"),
        ),
        ("pins", Path("/opt/supernova/pins.json"), Path("/opt/supernova/pins.json")),
        (
            "sandbox_sentinel",
            Path("/opt/supernova/sandbox_sentinel.txt"),
            Path("/opt/supernova/sandbox_sentinel.txt"),
        ),
    ):
        binaries[name] = artifact(build_path, runtime_path.as_posix())

    body: dict[str, Any] = {
        "schema": SCHEMA,
        "lean": {
            "binary": binaries["lean"],
            "githash": command_stdout([str(lean), "--githash"]),
            "sysroot": runtime_sysroot.as_posix(),
            "version": command_stdout([str(lean), "--version"]),
        },
        "dependency_graph": dependency_graph(template),
        "manifest_sha256": file_sha256(template / "lake-manifest.json"),
        "module_environment": normalized_environment(
            build_sysroot=build_sysroot,
            runtime_sysroot=runtime_sysroot,
        ),
        "artifacts": {
            "binaries": binaries,
            "trusted_oleans": trusted_oleans,
        },
        "policy": {
            "direct_lean_argv": [
                "/opt/lean/bin/lean",
                "-R",
                "{cell_root}",
                "-o",
                "{olean_path}",
                "-t",
                "0",
                "-DwarningAsError=true",
                "-M",
                "0",
                "{source_path}",
            ],
            "parser_mode": "PINNED_LEAN_PARSER_WITHOUT_ELABORATION",
            "parser_argv": [
                "/opt/supernova/bin/parse_product",
                "{product_source_path}",
            ],
            "permitted_axioms": list(PERMITTED_AXIOMS),
            "primitive_export_targets": list(PRIMITIVE_TARGETS),
            "runtime_forbidden_commands": ["git", "lake"],
            "trusted_challenge_warning_as_error": False,
            "theorem_target_policy": "FROZEN_PROBLEM_AUTHORITY_ONLY",
        },
    }
    body["root_sha256"] = hashlib.sha256(canonical_bytes(body)).hexdigest()
    return body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--build-sysroot", type=Path, required=True)
    parser.add_argument("--runtime-sysroot", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    lock = build_lock(args)
    args.output.write_text(
        json.dumps(lock, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
