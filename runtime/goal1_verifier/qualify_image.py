from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

REQUEST_SCHEMA = "supernova.goal1.verifier-container-request.v1"
RESPONSE_SCHEMA = "supernova.goal1.verifier-container-response.v1"
PERMITTED_AXIOMS = ["propext", "Quot.sound", "Classical.choice"]
INSIDE_SENTINEL = "SUPERNOVA_SANDBOX_SENTINEL_20260830_BOUNDED"
HOST_SENTINEL = "SUPERNOVA_HOST_SENTINEL_MUST_NEVER_CROSS_20260830"
HOST_SENTINEL_PATH = "/tmp/supernova_goal1_host_sentinel.txt"
QUALIFICATION_PHASE_TIMEOUT_SECONDS = 120


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def blob_fields(name: str, value: bytes) -> dict[str, str]:
    return {
        f"{name}_b64": base64.b64encode(value).decode("ascii"),
        f"{name}_sha256": sha256(value),
    }


def docker_base(image: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--runtime",
        "runc",
        "--init",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "256",
        "--memory",
        "4g",
        "--cpus",
        "2",
        "--user",
        "10001:10001",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=536870912",
        "--tmpfs",
        "/run:rw,noexec,nosuid,size=16777216",
    ]


def run_container(
    image: str,
    request: dict[str, Any],
    *,
    phase: str,
    expected_exit: int,
) -> tuple[dict[str, Any], bytes]:
    raw_request = canonical_bytes(request)
    try:
        completed = subprocess.run(
            [*docker_base(image), "-i", image],
            input=raw_request,
            capture_output=True,
            check=False,
            timeout=QUALIFICATION_PHASE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"{phase}: timed out after {QUALIFICATION_PHASE_TIMEOUT_SECONDS} seconds"
        ) from exc
    if completed.returncode != expected_exit:
        raise RuntimeError(
            f"verifier exit {completed.returncode}, expected {expected_exit}: "
            + (completed.stdout + b"\n" + completed.stderr).decode("utf-8", "replace")[
                :4000
            ]
        )
    try:
        response = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("verifier returned non-JSON output") from exc
    if not isinstance(response, dict) or response.get("schema") != RESPONSE_SCHEMA:
        raise RuntimeError("verifier response schema changed")
    if canonical_bytes(response) != completed.stdout:
        raise RuntimeError("verifier response is not canonical JSON")
    return response, completed.stderr


def decode_export(response: dict[str, Any]) -> bytes:
    try:
        value = base64.b64decode(response["solution_export_b64"], validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("EXPORTED response lacks a canonical export") from exc
    if sha256(value) != response.get("solution_export_sha256"):
        raise RuntimeError("EXPORTED response digest mismatch")
    return value


def assert_runtime_inventory(image: str) -> dict[str, Any]:
    script = """
set -eu
! command -v lake
! command -v git
test "$(/opt/lean/bin/lean --short-version)" = "4.33.1"
test "$(/opt/lean/bin/lean --githash)" = "819816b2e0a3bf405af45ae5c7af2491d8f5bee6"
test "$(/opt/lean/bin/lean --print-prefix)" = "/opt/lean"
test -x /opt/supernova/bin/lean4export
test -x /opt/supernova/bin/check_exports
test -x /opt/supernova/bin/nanoda_bin
test -x /opt/supernova/bin/parse_product
test -f /opt/supernova/VERIFIER_RUNTIME_LOCK.json
cat /opt/supernova/VERIFIER_RUNTIME_LOCK.json
""".strip()
    try:
        completed = subprocess.run(
            [*docker_base(image), "--entrypoint", "/bin/sh", image, "-c", script],
            capture_output=True,
            check=False,
            timeout=QUALIFICATION_PHASE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "runtime-inventory: timed out after "
            f"{QUALIFICATION_PHASE_TIMEOUT_SECONDS} seconds"
        ) from exc
    if completed.returncode != 0:
        raise RuntimeError(
            "runtime inventory failed: "
            + completed.stderr.decode("utf-8", "replace")[:2000]
        )
    value = json.loads(completed.stdout.decode("utf-8"))
    if not isinstance(value, dict) or value.get("schema") != (
        "supernova.goal1.verifier-runtime-lock.v1"
    ):
        raise RuntimeError("runtime lock schema changed")
    body = dict(value)
    expected = body.pop("root_sha256", None)
    if expected != sha256(canonical_bytes(body)):
        raise RuntimeError("runtime lock root digest is invalid")
    return value


def qualify(image: str) -> dict[str, str]:
    runtime_lock = assert_runtime_inventory(image)
    policy = runtime_lock.get("policy")
    if not isinstance(policy, dict):
        raise TypeError("runtime policy is missing")
    if policy.get("parser_mode") != "PINNED_LEAN_PARSER_WITHOUT_ELABORATION":
        raise RuntimeError("parser-only policy is not frozen")
    if policy.get("direct_lean_argv", [None])[-2:] != ["0", "{source_path}"]:
        raise RuntimeError("runtime lock does not bind cgroup-only memory authority")

    product_name = "SupernovaProduct.P_" + ("a" * 64) + ".a00"
    parse_only_source = f"""theorem {product_name} : True := by
  run_tac
    let inside <- IO.FS.readFile "/opt/supernova/sandbox_sentinel.txt"
    IO.println inside
  trivial
""".encode()
    parse_request: dict[str, Any] = {
        "expected_name": product_name,
        "mode": "parse_product",
        "schema": REQUEST_SCHEMA,
        **blob_fields("product_source", parse_only_source),
    }
    parsed, parser_stderr = run_container(
        image,
        parse_request,
        phase="parser-valid-declaration",
        expected_exit=0,
    )
    if parsed.get("status") != "PARSED":
        raise RuntimeError(f"parser-only mode rejected a valid declaration: {parsed}")
    parser_artifact = canonical_bytes(parsed) + parser_stderr
    if INSIDE_SENTINEL.encode("utf-8") in parser_artifact:
        raise RuntimeError("parser-only mode executed candidate elaboration")

    second_declaration = parse_only_source + b"\nlemma extra : True := by trivial\n"
    two_request: dict[str, Any] = {
        "expected_name": product_name,
        "mode": "parse_product",
        "schema": REQUEST_SCHEMA,
        **blob_fields("product_source", second_declaration),
    }
    rejected_parse, _ = run_container(
        image,
        two_request,
        phase="parser-reject-extra-declaration",
        expected_exit=10,
    )
    if rejected_parse.get("status") != "INVALID":
        raise RuntimeError("parser-only mode accepted two declarations")

    forbidden_source = f"theorem {product_name} : True := by\n  sorry\n".encode()
    forbidden_request: dict[str, Any] = {
        "expected_name": product_name,
        "mode": "parse_product",
        "schema": REQUEST_SCHEMA,
        **blob_fields("product_source", forbidden_source),
    }
    rejected_forbidden, _ = run_container(
        image,
        forbidden_request,
        phase="parser-reject-forbidden-syntax",
        expected_exit=10,
    )
    if rejected_forbidden.get("status") != "INVALID":
        raise RuntimeError("parser-only mode accepted frozen forbidden syntax")

    theorem = "supernova_benign"
    benign_source = (
        b"import Mathlib\n"
        b"theorem supernova_benign : (1 : Nat) + 1 = 2 := by\n"
        b"  norm_num\n"
    )
    benign_request: dict[str, Any] = {
        "mode": "elaborate",
        "permitted_axioms": PERMITTED_AXIOMS,
        "schema": REQUEST_SCHEMA,
        "theorem_names": [theorem],
        **blob_fields("solution_source", benign_source),
    }
    elaborated, elaborator_stderr = run_container(
        image,
        benign_request,
        phase="benign-elaboration",
        expected_exit=0,
    )
    if elaborated.get("status") != "EXPORTED":
        raise RuntimeError(f"benign elaboration did not export: {elaborated}")
    solution_export = decode_export(elaborated)
    challenge_source = (
        b"import Mathlib\ntheorem supernova_benign : (1 : Nat) + 1 = 2 := by\n  sorry\n"
    )
    check_request: dict[str, Any] = {
        "mode": "check",
        "permitted_axioms": PERMITTED_AXIOMS,
        "schema": REQUEST_SCHEMA,
        "theorem_names": [theorem],
        **blob_fields("challenge_source", challenge_source),
        **blob_fields("solution_export", solution_export),
    }
    checked, checker_stderr = run_container(
        image,
        check_request,
        phase="benign-independent-check",
        expected_exit=0,
    )
    if checked.get("status") != "VALID":
        raise RuntimeError(f"benign checker did not return VALID: {checked}")
    if checked.get("solution_export_sha256") != sha256(solution_export):
        raise RuntimeError("checker did not join the exact solution export")

    hostile_source = f'''import Mathlib
open Lean Elab Command
elab "supernovaSandboxProbe" : command => do
  let inside ← IO.FS.readFile "/opt/supernova/sandbox_sentinel.txt"
  logInfo m!"INSIDE_SENTINEL={{inside.trim}}"
  let hostReadable ←
    try
      discard <| IO.FS.readFile "{HOST_SENTINEL_PATH}"
      pure true
    catch _ =>
      pure false
  if hostReadable then
    throwError "HOST_SENTINEL_READABLE"
  throwError "HOST_SENTINEL_UNREADABLE"
supernovaSandboxProbe
theorem supernova_hostile : True := by
  trivial
'''.encode()
    hostile_request: dict[str, Any] = {
        "mode": "elaborate",
        "permitted_axioms": PERMITTED_AXIOMS,
        "schema": REQUEST_SCHEMA,
        "theorem_names": ["supernova_hostile"],
        **blob_fields("solution_source", hostile_source),
    }
    host_path = Path(tempfile.gettempdir()) / "supernova_goal1_host_sentinel.txt"
    host_path.write_text(HOST_SENTINEL, encoding="utf-8")
    try:
        hostile, hostile_stderr = run_container(
            image,
            hostile_request,
            phase="hostile-metaprogram-containment",
            expected_exit=10,
        )
    finally:
        host_path.unlink(missing_ok=True)
    hostile_raw = canonical_bytes(hostile) + hostile_stderr
    if hostile.get("status") != "INVALID":
        raise RuntimeError(f"hostile elaboration was not contained: {hostile}")
    diagnostic = hostile.get("diagnostic")
    if not isinstance(diagnostic, str):
        raise TypeError("hostile elaboration lacks a diagnostic")
    if (
        INSIDE_SENTINEL not in diagnostic
        or "HOST_SENTINEL_UNREADABLE" not in diagnostic
    ):
        raise RuntimeError("hostile metaprogram execution was not proven")
    if HOST_SENTINEL.encode("utf-8") in hostile_raw:
        raise RuntimeError("host-only sentinel crossed the sandbox boundary")
    for artifact in (
        solution_export,
        canonical_bytes(checked),
        elaborator_stderr,
        checker_stderr,
    ):
        if INSIDE_SENTINEL.encode("utf-8") in artifact:
            raise RuntimeError("sandbox sentinel leaked into a trusted artifact")
        if HOST_SENTINEL.encode("utf-8") in artifact:
            raise RuntimeError("host-only sentinel leaked into a trusted artifact")

    return {
        "benign_solution_export_sha256": sha256(solution_export),
        "hostile_request_sha256": sha256(canonical_bytes(hostile_request)),
        "image": image,
        "parser_only_request_sha256": sha256(canonical_bytes(parse_request)),
        "runtime_lock_sha256": str(runtime_lock["root_sha256"]),
        "status": "QUALIFIED",
    }


def github_command_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    exit_code = 0
    try:
        result = qualify(args.image)
    except Exception as exc:  # noqa: BLE001 - qualification must persist its cause
        result = {
            "error": str(exc),
            "error_type": type(exc).__name__,
            "image": args.image,
            "status": "FAILED",
        }
        message = github_command_escape(f"{result['error_type']}: {result['error']}")
        print("::error file=runtime/goal1_verifier/qualify_image.py::" + message)
        exit_code = 1
    raw = json.dumps(result, allow_nan=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(raw, encoding="utf-8")
    print(raw, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
