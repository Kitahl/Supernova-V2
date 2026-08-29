from __future__ import annotations

import argparse
from base64 import b64encode
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import Any, Mapping, Sequence

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .evidence_bridge import HermeticContextReceipt
from .execution_authority import (
    AUTHORITY_SCHEMA,
    HERMETIC_CONTEXT_MODE,
    PRODUCTION_RECEIPT_SCHEMA,
    ValidatedExecutionAuthority,
    _PREFLIGHT_CHECKS,
    _repository_root,
    _validate_authority_artifact,
    canonical_sha256,
    load_execution_authority,
    signed_bytes,
)


PREFLIGHT_SCHEMA = "supernova.hermetic-preflight-receipt.v1"
PREFLIGHT_RESPONSE_SCHEMA = "supernova.hermetic-preflight-response.v1"
PREFLIGHT_VALIDATION_SCHEMA = "supernova.preflight-validation-record.v1"
TRUST_ROOT_SCHEMA = "supernova.confirmatory-trust-root.v1"
LAUNCHER_SCHEMA = "supernova.hermetic-launcher.v1"
CAPACITY_BINDING_SCHEMA = "supernova.confirmatory-capacity-binding.v1"
LAUNCHER_RELATIVE_PATH = Path("goal1") / "CONFIRMATORY_EXECUTOR_LAUNCHER.json"
CAPACITY_BINDING_RELATIVE_PATH = Path("goal1") / "CONFIRMATORY_CAPACITY_BINDING.json"
RUNTIME_RELATIVE_PATH = Path("goal1") / "CONFIRMATORY_RUNTIME.json"
BUILD_LOCK_RELATIVE_PATH = Path("runtime") / "goal1_hermetic_executor" / "BUILD_LOCK.json"
PUBLICATION_RELATIVE_PATH = (
    Path("runtime") / "goal1_hermetic_executor" / "PUBLISHED_IMAGE.json"
)
EMPTY_CONTEXT_SHA256 = sha256(b"").hexdigest()
_TMPFS = {
    "/run": "rw,noexec,nosuid,size=16777216",
    "/tmp": "rw,noexec,nosuid,size=536870912",
}
_LAUNCHER_CONFIG_FIELDS = frozenset(
    {
        "command",
        "container_image_ref",
        "container_user",
        "exact_model_version",
        "generation_settings",
        "image_environment",
        "inference_runtime_sha256",
        "max_output_bytes",
        "memory_bytes",
        "model_provider",
        "model_weights_sha256",
        "nano_cpus",
        "schema",
        "timeout_seconds",
        "tokenizer_sha256",
    }
)
_CAPACITY_BINDING_FIELDS = frozenset(
    {
        "concurrency",
        "executor_image_ref",
        "launcher_artifact_sha256",
        "model_slot",
        "platform",
        "pool_id",
        "pool_instance_count",
        "schema",
        "selection_after_manifest",
        "verifier_slot",
    }
)
_MODEL_SLOT_FIELDS = frozenset(
    {
        "gpu_device_requests",
        "max_output_bytes",
        "memory_bytes",
        "nano_cpus",
        "network",
        "pids_limit",
        "runtime",
        "timeout_seconds",
    }
)
_VERIFIER_SLOT_FIELDS = frozenset(
    {
        "host_wall_clock_milliseconds",
        "lean_memory_megabytes",
        "process_tree_kill_on_limit",
        "stderr_max_bytes",
        "stdout_max_bytes",
        "timeout_or_truncation_decision",
    }
)
_CONCURRENCY_FIELDS = frozenset(
    {"max_model_dispatches", "max_verifier_processes", "protocol_rule"}
)
_PUBLICATION_FIELDS = frozenset(
    {
        "build_lock_sha256",
        "evidence_artifact_digest",
        "executor_sha256",
        "image_digest",
        "image_ref",
        "llama_cli_sha256",
        "model_sha256",
        "platform",
        "publication_status",
        "schema",
        "source_commit",
        "workflow_run_id",
        "workflow_url",
    }
)


class SupervisorError(RuntimeError):
    """The host could not prove the required hermetic lifecycle."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _private_key(raw: bytes, field: str) -> Ed25519PrivateKey:
    if type(raw) is not bytes or len(raw) != 32:
        raise ValueError(f"{field} must be one raw 32-byte Ed25519 private key")
    return Ed25519PrivateKey.from_private_bytes(raw)


def _public_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _b64(value: bytes) -> str:
    return b64encode(value).decode("ascii")


def _sha256_hex(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64:
        raise ValueError(f"{field} must be 64 lowercase hexadecimal characters")
    if any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field} must be 64 lowercase hexadecimal characters")
    return value


def _token(value: object, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty trimmed string")
    return value


def _json_object(value: bytes, field: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SupervisorError(f"{field} is not one UTF-8 JSON object") from exc
    if type(decoded) is not dict:
        raise SupervisorError(f"{field} is not one exact JSON object")
    return decoded


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _load_exact_json_object(path: Path, field: str) -> dict[str, Any]:
    if not isinstance(path, Path):
        raise TypeError(f"{field} path must be exact pathlib.Path")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite_json,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} must be one readable UTF-8 JSON object") from exc
    if type(value) is not dict:
        raise ValueError(f"{field} must be one exact JSON object")
    return value


@dataclass(frozen=True)
class HermeticLauncher:
    """Immutable host-side description of one local model executor."""

    container_image_ref: str
    command: tuple[str, ...]
    inference_runtime_sha256: str
    model_weights_sha256: str
    tokenizer_sha256: str
    exact_model_version: str
    model_provider: str
    generation_settings: dict[str, Any]
    image_environment: tuple[str, ...]
    container_user: str
    memory_bytes: int
    nano_cpus: int
    timeout_seconds: int
    max_output_bytes: int

    def __post_init__(self) -> None:
        ref = _token(self.container_image_ref, "container_image_ref")
        if "@sha256:" not in ref:
            raise ValueError("container_image_ref must be pinned by repo@sha256 digest")
        _sha256_hex(ref.rsplit("@sha256:", 1)[1], "container_image_ref digest")
        if type(self.command) is not tuple or not self.command:
            raise ValueError("command must be one non-empty tuple")
        for index, value in enumerate(self.command):
            _token(value, f"command[{index}]")
        for field in (
            "inference_runtime_sha256",
            "model_weights_sha256",
            "tokenizer_sha256",
        ):
            _sha256_hex(getattr(self, field), field)
        _token(self.exact_model_version, "exact_model_version")
        _token(self.model_provider, "model_provider")
        if type(self.generation_settings) is not dict:
            raise ValueError("generation_settings must be one exact object")
        frozen_settings = json.loads(
            json.dumps(self.generation_settings, allow_nan=False, sort_keys=True)
        )
        object.__setattr__(self, "generation_settings", frozen_settings)
        if type(self.image_environment) is not tuple:
            raise ValueError("image_environment must be one exact tuple")
        allowed_environment_entries = {
            "HOME=/nonexistent",
            "LANG=C.UTF-8",
            "LC_ALL=C.UTF-8",
            "OMP_NUM_THREADS=1",
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "PYTHONPATH=/opt/supernova",
            "PYTHONUNBUFFERED=1",
            "RUST_BACKTRACE=0",
            "TOKENIZERS_PARALLELISM=false",
            "TZ=UTC",
        }
        names: list[str] = []
        for index, entry in enumerate(self.image_environment):
            entry = _token(entry, f"image_environment[{index}]")
            if entry not in allowed_environment_entries:
                raise ValueError(f"image environment entry is not allowed: {entry}")
            name, _value = entry.split("=", 1)
            names.append(name)
        if len(names) != len(set(names)):
            raise ValueError("image_environment contains a duplicate name")
        user = _token(self.container_user, "container_user")
        if user.split(":", 1)[0].lower() in {"0", "root"}:
            raise ValueError("container_user must be a non-root identity")
        for field in (
            "memory_bytes",
            "nano_cpus",
            "timeout_seconds",
            "max_output_bytes",
        ):
            if type(getattr(self, field)) is not int or getattr(self, field) <= 0:
                raise ValueError(f"{field} must be a positive integer")

    @property
    def container_image_digest(self) -> str:
        return "sha256:" + self.container_image_ref.rsplit("@sha256:", 1)[1]

    @property
    def launcher_artifact_sha256(self) -> str:
        return canonical_sha256(
            {
                "cap_drop": ["ALL"],
                "command": list(self.command),
                "image_environment": list(self.image_environment),
                "image_ref": self.container_image_ref,
                "container_user": self.container_user,
                "max_output_bytes": self.max_output_bytes,
                "memory_bytes": self.memory_bytes,
                "nano_cpus": self.nano_cpus,
                "network": "none",
                "pids_limit": 256,
                "read_only_root": True,
                "schema": LAUNCHER_SCHEMA,
                "security_opt": ["no-new-privileges:true"],
                "timeout_seconds": self.timeout_seconds,
                "tmpfs": _TMPFS,
                "transport": "STDIN_STDOUT",
            }
        )

    @property
    def executor_artifact(self) -> dict[str, object]:
        return {
            "container_image_digest": self.container_image_digest,
            "fresh_process_per_attempt": True,
            "inference_runtime_sha256": self.inference_runtime_sha256,
            "launcher_artifact_sha256": self.launcher_artifact_sha256,
            "model_weights_sha256": self.model_weights_sha256,
            "network_policy": "NONE",
            "persistent_writable_state": "DISABLED",
            "raw_credentials_exposed_to_child": False,
            "tokenizer_sha256": self.tokenizer_sha256,
        }

    @property
    def executor_artifact_sha256(self) -> str:
        return canonical_sha256(self.executor_artifact)

    @property
    def generation_settings_sha256(self) -> str:
        return canonical_sha256(self.generation_settings)

    @property
    def model_identity_sha256(self) -> str:
        return canonical_sha256(
            {
                "exact_model_version": self.exact_model_version,
                "generation_settings_sha256": self.generation_settings_sha256,
                "model_provider": self.model_provider,
            }
        )


@dataclass(frozen=True)
class PreflightEvidence:
    receipt: dict[str, object]
    validation: dict[str, object]
    receipt_public_key: bytes


@dataclass(frozen=True)
class SealedExecutionAuthority:
    trust_root: dict[str, object]
    authority: dict[str, object]


@dataclass(frozen=True)
class SupervisedAttempt:
    response: bytes
    context_receipt: HermeticContextReceipt
    elapsed_milliseconds: int


@dataclass(frozen=True)
class _Lifecycle:
    response: bytes
    clean_image_sha256: str
    instance_nonce: str
    opened_at: str
    closed_at: str
    elapsed_milliseconds: int


def _invoke(
    argv: Sequence[str], *, input_bytes: bytes | None = None, timeout: int | None = None
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            list(argv),
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SupervisorError(f"host command failed: {argv[0]}") from exc


def _require_success(
    result: subprocess.CompletedProcess[bytes], field: str
) -> bytes:
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")[:500]
        raise SupervisorError(f"{field} failed: {detail}")
    return result.stdout


def _docker_runtime_identity() -> str:
    result = _invoke(["docker", "version", "--format", "{{json .}}"])
    payload = _json_object(
        _require_success(result, "docker version"), "docker version"
    )
    return canonical_sha256(payload)


def _image_identity(launcher: HermeticLauncher) -> str:
    result = _invoke(["docker", "image", "inspect", launcher.container_image_ref])
    try:
        decoded = json.loads(
            _require_success(result, "docker image inspect").decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SupervisorError("image inspect returned invalid JSON") from exc
    if type(decoded) is not list or len(decoded) != 1 or type(decoded[0]) is not dict:
        raise SupervisorError("image inspect returned an unexpected shape")
    payload = decoded[0]
    image_id = payload.get("Id")
    repo_digests = payload.get("RepoDigests")
    if type(image_id) is not str or not image_id.startswith("sha256:"):
        raise SupervisorError("image inspect did not return an immutable image id")
    if type(repo_digests) is not list or launcher.container_image_ref not in repo_digests:
        raise SupervisorError("local image does not match the pinned repository digest")
    return image_id


def _create_argv(launcher: HermeticLauncher) -> list[str]:
    return [
        "docker",
        "create",
        "--network",
        "none",
        "--read-only",
        "--init",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "256",
        "--ipc",
        "none",
        "--uts",
        "private",
        "--user",
        launcher.container_user,
        "--runtime",
        "runc",
        "--memory",
        str(launcher.memory_bytes),
        "--cpus",
        format(launcher.nano_cpus / 1_000_000_000, ".9f"),
        "--tmpfs",
        f"/tmp:{_TMPFS['/tmp']}",
        "--tmpfs",
        f"/run:{_TMPFS['/run']}",
        "--interactive",
        launcher.container_image_ref,
        *launcher.command,
    ]


def _container_object(container_id: str) -> dict[str, Any]:
    result = _invoke(["docker", "inspect", container_id])
    raw = _require_success(result, "docker inspect")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SupervisorError("docker inspect returned invalid JSON") from exc
    if type(payload) is not list or len(payload) != 1 or type(payload[0]) is not dict:
        raise SupervisorError("docker inspect returned an unexpected shape")
    return payload[0]


def _clean_snapshot(
    inspection: Mapping[str, Any], launcher: HermeticLauncher, image_id: str,
    docker_runtime_identity_sha256: str,
) -> dict[str, object]:
    host = inspection.get("HostConfig")
    config = inspection.get("Config")
    mounts = inspection.get("Mounts")
    if type(host) is not dict or type(config) is not dict or type(mounts) is not list:
        raise SupervisorError("container inspection omitted security configuration")
    security = host.get("SecurityOpt") or []
    cap_drop = host.get("CapDrop") or []
    cap_add = host.get("CapAdd") or []
    devices = host.get("Devices") or []
    device_requests = host.get("DeviceRequests") or []
    tmpfs = host.get("Tmpfs") or {}
    binds = host.get("Binds") or []
    restart = host.get("RestartPolicy") or {}
    if (
        host.get("NetworkMode") != "none"
        or host.get("ReadonlyRootfs") is not True
        or host.get("Privileged") is not False
        or sorted(cap_drop) != ["ALL"]
        or cap_add != []
        or devices != []
        or device_requests != []
        or sorted(security) != ["no-new-privileges:true"]
        or host.get("PidsLimit") != 256
        or host.get("Memory") != launcher.memory_bytes
        or host.get("NanoCpus") != launcher.nano_cpus
        or host.get("IpcMode") != "none"
        or host.get("PidMode") != ""
        or host.get("UTSMode") != "private"
        or host.get("UsernsMode") not in {None, ""}
        or host.get("CgroupnsMode") not in {None, "", "private"}
        or host.get("PublishAllPorts") is not False
        or (host.get("PortBindings") or {}) != {}
        or (host.get("Links") or []) != []
        or (host.get("ExtraHosts") or []) != []
        or (host.get("GroupAdd") or []) != []
        or (host.get("VolumesFrom") or []) != []
        or host.get("AutoRemove") is not False
        or host.get("OomKillDisable") not in {None, False}
        or host.get("Init") is not True
        or host.get("Runtime") != "runc"
        or host.get("Isolation") not in {None, "", "default"}
        or restart != {"MaximumRetryCount": 0, "Name": "no"}
        or tmpfs != _TMPFS
        or binds != []
        or mounts != []
        or config.get("OpenStdin") is not True
        or config.get("Tty") is not False
        or config.get("Cmd") != list(launcher.command)
        or (config.get("Env") or []) != list(launcher.image_environment)
        or config.get("User") != launcher.container_user
        or inspection.get("Image") != image_id
    ):
        raise SupervisorError("container security configuration drifted from the launcher")
    return {
        "auto_remove": host.get("AutoRemove"),
        "cap_add": cap_add,
        "cap_drop": sorted(cap_drop),
        "cgroupns_mode": host.get("CgroupnsMode") or "",
        "command": config.get("Cmd"),
        "device_requests": device_requests,
        "devices": devices,
        "docker_runtime_identity_sha256": docker_runtime_identity_sha256,
        "entrypoint": config.get("Entrypoint"),
        "environment": config.get("Env") or [],
        "image_id": image_id,
        "image_ref": launcher.container_image_ref,
        "ipc_mode": host.get("IpcMode"),
        "labels": config.get("Labels") or {},
        "memory_bytes": host.get("Memory"),
        "mounts": mounts,
        "nano_cpus": host.get("NanoCpus"),
        "network_mode": host.get("NetworkMode"),
        "pid_mode": host.get("PidMode"),
        "pids_limit": host.get("PidsLimit"),
        "privileged": host.get("Privileged"),
        "read_only_root": host.get("ReadonlyRootfs"),
        "runtime": host.get("Runtime"),
        "security_opt": sorted(security),
        "tmpfs": tmpfs,
        "user": config.get("User"),
        "userns_mode": host.get("UsernsMode") or "",
        "uts_mode": host.get("UTSMode"),
        "working_dir": config.get("WorkingDir") or "",
    }


def _observed_teardown(
    container_id: str, *, primary_error: BaseException | None = None
) -> None:
    try:
        removed = _invoke(["docker", "rm", "--force", container_id])
        _require_success(removed, "docker rm")
        probe = _invoke(["docker", "inspect", container_id])
        detail = probe.stderr.decode("utf-8", errors="replace").lower()
        if probe.returncode == 0 or (
            "no such container" not in detail and "no such object" not in detail
        ):
            raise SupervisorError("post-removal absence was not authenticated")
    except BaseException as cleanup_error:
        failure = SupervisorError("container cleanup was not observed")
        if primary_error is not None:
            raise failure from primary_error
        raise failure from cleanup_error


def _run_fresh_container(
    launcher: HermeticLauncher,
    request: bytes,
    *,
    expected_clean_image_sha256: str | None = None,
) -> _Lifecycle:
    if type(request) is not bytes:
        raise TypeError("request must be exact bytes")
    docker_runtime_identity_sha256 = _docker_runtime_identity()
    image_id = _image_identity(launcher)
    created = _invoke(_create_argv(launcher))
    container_id = _require_success(created, "docker create").decode("ascii").strip()
    if len(container_id) != 64 or any(
        char not in "0123456789abcdef" for char in container_id
    ):
        raise SupervisorError("docker create did not return one full container id")
    try:
        inspection = _container_object(container_id)
        snapshot = _clean_snapshot(
            inspection, launcher, image_id, docker_runtime_identity_sha256
        )
        clean_sha = canonical_sha256(snapshot)
        if (
            expected_clean_image_sha256 is not None
            and clean_sha != expected_clean_image_sha256
        ):
            raise SupervisorError("clean container image/configuration digest drifted")
        nonce = secrets.token_hex(32)
        opened_at = _utc_now()
        started = _invoke(
            ["docker", "start", "--attach", "--interactive", container_id],
            input_bytes=request,
            timeout=launcher.timeout_seconds,
        )
        response = _require_success(started, "docker start")
        closed_at = _utc_now()
        if len(response) > launcher.max_output_bytes:
            raise SupervisorError("executor response exceeded the frozen output limit")
        stopped = _container_object(container_id).get("State")
        if (
            type(stopped) is not dict
            or stopped.get("Status") != "exited"
            or stopped.get("ExitCode") != 0
        ):
            raise SupervisorError("executor did not terminate cleanly")
        opened = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        closed = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
        lifecycle = _Lifecycle(
            response=response,
            clean_image_sha256=clean_sha,
            instance_nonce=nonce,
            opened_at=opened_at,
            closed_at=closed_at,
            elapsed_milliseconds=max(
                0, int((closed - opened).total_seconds() * 1000)
            ),
        )
    except BaseException as primary_error:
        _observed_teardown(container_id, primary_error=primary_error)
        raise
    _observed_teardown(container_id)
    return lifecycle


def run_hermetic_preflight(
    launcher: HermeticLauncher,
    *,
    issuer_id: str,
    validator_id: str,
    receipt_private_key: bytes,
) -> PreflightEvidence:
    """Observe one real empty-context lifecycle and issue its public receipt."""

    issuer_id = _token(issuer_id, "issuer_id")
    validator_id = _token(validator_id, "validator_id")
    if "SIMULATION" in issuer_id.upper() or "CHAT" in issuer_id.upper():
        raise ValueError("simulation and recurring-chat issuers are non-credit")
    receipt_key = _private_key(receipt_private_key, "receipt_private_key")
    request = json.dumps(
        {
            "context": [],
            "executor_artifact_sha256": launcher.executor_artifact_sha256,
            "model_identity_sha256": launcher.model_identity_sha256,
            "operation": "PREFLIGHT",
            "schema": "supernova.hermetic-preflight-request.v1",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    lifecycle = _run_fresh_container(launcher, request)
    response = _json_object(lifecycle.response, "preflight response")
    if response != {
        "executor_artifact_sha256": launcher.executor_artifact_sha256,
        "model_identity_sha256": launcher.model_identity_sha256,
        "schema": PREFLIGHT_RESPONSE_SCHEMA,
        "status": "READY",
    }:
        raise SupervisorError("executor preflight response did not match the host challenge")
    body = {
        "clean_image_sha256": lifecycle.clean_image_sha256,
        "closed_at": lifecycle.closed_at,
        "context_mode": HERMETIC_CONTEXT_MODE,
        "executor_artifact_sha256": launcher.executor_artifact_sha256,
        "fresh_process_observed": True,
        "instance_nonce": lifecycle.instance_nonce,
        "issuer_id": issuer_id,
        "model_identity_sha256": launcher.model_identity_sha256,
        "network_policy": "NONE",
        "opened_at": lifecycle.opened_at,
        "persistent_writable_state": "DISABLED",
        "schema": PREFLIGHT_SCHEMA,
        "teardown_observed": True,
    }
    receipt = dict(body)
    receipt["signature"] = _b64(receipt_key.sign(signed_bytes(PREFLIGHT_SCHEMA, body)))
    validation = {
        "checks": list(_PREFLIGHT_CHECKS),
        "receipt_sha256": canonical_sha256(receipt),
        "schema": PREFLIGHT_VALIDATION_SCHEMA,
        "validated_at": _utc_now(),
        "validator_id": validator_id,
        "verdict": "PASS",
    }
    return PreflightEvidence(
        receipt=receipt,
        validation=validation,
        receipt_public_key=_public_bytes(receipt_key),
    )


def _seal_execution_authority(
    protocol: Mapping[str, Any],
    goal1: Mapping[str, Any],
    launcher: HermeticLauncher,
    preflight: PreflightEvidence,
    *,
    authority_id: str,
    root_key_id: str,
    root_private_key: bytes,
    receipt_issuer_id: str,
    pool_id: str,
    capacity_binding_sha256: str,
) -> SealedExecutionAuthority:
    """Sign public activation artifacts after a host-observed preflight."""

    if type(protocol) is not dict or type(goal1) is not dict:
        raise TypeError("protocol and goal1 must be exact dictionaries")
    if type(preflight) is not PreflightEvidence:
        raise TypeError("preflight must be exact PreflightEvidence")
    authority_id = _token(authority_id, "authority_id")
    root_key_id = _token(root_key_id, "root_key_id")
    receipt_issuer_id = _token(receipt_issuer_id, "receipt_issuer_id")
    pool_id = _token(pool_id, "pool_id")
    _sha256_hex(capacity_binding_sha256, "capacity_binding_sha256")
    if preflight.receipt.get("issuer_id") != receipt_issuer_id:
        raise ValueError("preflight issuer differs from execution receipt issuer")
    if preflight.receipt.get("executor_artifact_sha256") != launcher.executor_artifact_sha256:
        raise ValueError("preflight binds a different executor")
    if preflight.receipt.get("model_identity_sha256") != launcher.model_identity_sha256:
        raise ValueError("preflight binds a different model identity")
    signature = preflight.receipt.get("signature")
    if type(signature) is not str:
        raise ValueError("preflight signature is absent")
    try:
        Ed25519PublicKey.from_public_bytes(preflight.receipt_public_key).verify(
            __import__("base64").b64decode(signature, validate=True),
            signed_bytes(
                PREFLIGHT_SCHEMA,
                {key: preflight.receipt[key] for key in preflight.receipt if key != "signature"},
            ),
        )
    except Exception as exc:
        raise ValueError("preflight signature is invalid") from exc
    if preflight.validation != {
        "checks": list(_PREFLIGHT_CHECKS),
        "receipt_sha256": canonical_sha256(preflight.receipt),
        "schema": PREFLIGHT_VALIDATION_SCHEMA,
        "validated_at": preflight.validation.get("validated_at"),
        "validator_id": preflight.validation.get("validator_id"),
        "verdict": "PASS",
    }:
        raise ValueError("preflight validation record is not the complete PASS record")
    _token(preflight.validation.get("validated_at"), "validated_at")
    _token(preflight.validation.get("validator_id"), "validator_id")

    root_key = _private_key(root_private_key, "root_private_key")
    root_public = _public_bytes(root_key)
    trust_root = {
        "ed25519_public_key_b64": _b64(root_public),
        "root_key_id": root_key_id,
        "schema": TRUST_ROOT_SCHEMA,
    }
    schedule = json.loads(json.dumps(protocol["sealed_rules"]["deterministic_schedule"]))
    pool = {
        "capacity_binding_sha256": capacity_binding_sha256,
        "policy": "PINNED_SINGLE_HERMETIC_POOL",
        "pool_id": pool_id,
        "selection_after_manifest": "BLOCKED",
    }
    body = {
        "authority_id": authority_id,
        "context_mode": HERMETIC_CONTEXT_MODE,
        "exact_model_version": launcher.exact_model_version,
        "executor_artifact": launcher.executor_artifact,
        "executor_artifact_sha256": launcher.executor_artifact_sha256,
        "generation_settings": launcher.generation_settings,
        "generation_settings_sha256": launcher.generation_settings_sha256,
        "goal1_authority_sha256": canonical_sha256(goal1),
        "model_provider": launcher.model_provider,
        "preflight_receipt": preflight.receipt,
        "preflight_receipt_sha256": canonical_sha256(preflight.receipt),
        "preflight_validation_record": preflight.validation,
        "preflight_validation_record_sha256": canonical_sha256(preflight.validation),
        "protocol_rules_sha256": protocol["sealed_rules_sha256"],
        "provider_attested_fresh_empty_context_capability": False,
        "receipt_issuer_id": receipt_issuer_id,
        "receipt_schema": PRODUCTION_RECEIPT_SCHEMA,
        "receipt_verification_key_sha256": sha256(preflight.receipt_public_key).hexdigest(),
        "receipt_verification_public_key_b64": _b64(preflight.receipt_public_key),
        "root_key_id": root_key_id,
        "scheduling_policy": schedule,
        "scheduling_policy_sha256": canonical_sha256(schedule),
        "schema": AUTHORITY_SCHEMA,
        "serving_pool_policy": pool,
        "serving_pool_policy_sha256": canonical_sha256(pool),
    }
    authority = dict(body)
    authority["signature"] = _b64(root_key.sign(signed_bytes(AUTHORITY_SCHEMA, body)))
    _validate_authority_artifact(
        authority,
        protocol=protocol,
        goal1=goal1,
        root_key_id=root_key_id,
        root_public_key=root_public,
    )
    return SealedExecutionAuthority(trust_root=trust_root, authority=authority)


def provision_execution_authority(
    protocol: Mapping[str, Any],
    goal1: Mapping[str, Any],
    launcher: HermeticLauncher,
    *,
    authority_id: str,
    root_key_id: str,
    root_private_key: bytes,
    receipt_issuer_id: str,
    receipt_private_key: bytes,
    validator_id: str,
    pool_id: str,
    capacity_binding_sha256: str,
) -> SealedExecutionAuthority:
    """Run the production Docker preflight and then seal only its observation."""

    preflight = run_hermetic_preflight(
        launcher,
        issuer_id=receipt_issuer_id,
        validator_id=validator_id,
        receipt_private_key=receipt_private_key,
    )
    return _seal_execution_authority(
        protocol,
        goal1,
        launcher,
        preflight,
        authority_id=authority_id,
        root_key_id=root_key_id,
        root_private_key=root_private_key,
        receipt_issuer_id=receipt_issuer_id,
        pool_id=pool_id,
        capacity_binding_sha256=capacity_binding_sha256,
    )


def run_supervised_attempt(
    launcher: HermeticLauncher,
    authority: ValidatedExecutionAuthority,
    request: bytes,
    *,
    receipt_private_key: bytes,
    confirmatory_manifest_sha256: str,
    run_id: str,
    protocol_dispatch_id: str,
    dispatch_id: str,
    problem_id: str,
    arm: str,
    attempt_index: int,
    sequence: int,
) -> SupervisedAttempt:
    """Execute one fresh, networkless attempt and sign the observed lifecycle."""

    if type(authority) is not ValidatedExecutionAuthority:
        raise TypeError("authority must be a validated fixed-checkout capability")
    if launcher.executor_artifact_sha256 != authority.executor_artifact_sha256:
        raise PermissionError("launcher differs from the validated executor artifact")
    if launcher.model_identity_sha256 != authority.model_identity_sha256:
        raise PermissionError("launcher differs from the validated model identity")
    receipt_key = _private_key(receipt_private_key, "receipt_private_key")
    if _public_bytes(receipt_key) != authority.receipt_public_key:
        raise PermissionError("receipt private key does not match execution authority")
    lifecycle = _run_fresh_container(
        launcher,
        request,
        expected_clean_image_sha256=authority.clean_image_sha256,
    )
    body = {
        "arm": arm,
        "attempt_index": attempt_index,
        "clean_image_sha256": lifecycle.clean_image_sha256,
        "closed_at": lifecycle.closed_at,
        "confirmatory_manifest_sha256": confirmatory_manifest_sha256,
        "dispatch_id": dispatch_id,
        "execution_authority_sha256": authority.authority_sha256,
        "executor_artifact_sha256": authority.executor_artifact_sha256,
        "initial_context_sha256": EMPTY_CONTEXT_SHA256,
        "instance_nonce": lifecycle.instance_nonce,
        "issuer_id": authority.issuer_id,
        "model_identity_sha256": authority.model_identity_sha256,
        "network_policy": "NONE",
        "opened_at": lifecycle.opened_at,
        "persistent_writable_state": "DISABLED",
        "problem_id": problem_id,
        "protocol_dispatch_id": protocol_dispatch_id,
        "request_artifact_sha256": sha256(request).hexdigest(),
        "response_artifact_sha256": sha256(lifecycle.response).hexdigest(),
        "run_id": run_id,
        "schema": PRODUCTION_RECEIPT_SCHEMA,
        "sequence": sequence,
        "teardown_observed": True,
    }
    signed = _b64(receipt_key.sign(signed_bytes(PRODUCTION_RECEIPT_SCHEMA, body)))
    receipt = HermeticContextReceipt(
        issuer_id=authority.issuer_id,
        execution_authority_sha256=authority.authority_sha256,
        confirmatory_manifest_sha256=confirmatory_manifest_sha256,
        model_identity_sha256=authority.model_identity_sha256,
        executor_artifact_sha256=authority.executor_artifact_sha256,
        run_id=run_id,
        protocol_dispatch_id=protocol_dispatch_id,
        dispatch_id=dispatch_id,
        problem_id=problem_id,
        arm=arm,
        attempt_index=attempt_index,
        sequence=sequence,
        instance_nonce=lifecycle.instance_nonce,
        clean_image_sha256=lifecycle.clean_image_sha256,
        initial_context_sha256=EMPTY_CONTEXT_SHA256,
        request_artifact_sha256=sha256(request).hexdigest(),
        response_artifact_sha256=sha256(lifecycle.response).hexdigest(),
        opened_at=lifecycle.opened_at,
        closed_at=lifecycle.closed_at,
        network_policy="NONE",
        persistent_writable_state="DISABLED",
        teardown_observed=True,
        signature=signed,
    )
    authority.verify_receipt_signature(
        receipt.signature, domain=PRODUCTION_RECEIPT_SCHEMA, body=receipt.body()
    )
    return SupervisedAttempt(
        response=lifecycle.response,
        context_receipt=receipt,
        elapsed_milliseconds=lifecycle.elapsed_milliseconds,
    )


def load_private_key_file(path: Path) -> bytes:
    """Read one raw host key without accepting text, environment, or child exposure."""

    if not isinstance(path, Path):
        raise TypeError("path must be exact pathlib.Path")
    raw = path.read_bytes()
    _private_key(raw, "private key file")
    return raw



def load_launcher_file(path: Path) -> HermeticLauncher:
    """Load one exact launcher configuration."""

    value = _load_exact_json_object(path, "launcher file")
    if set(value) != _LAUNCHER_CONFIG_FIELDS:
        raise ValueError("launcher file fields differ from the exact launcher schema")
    if value["schema"] != LAUNCHER_SCHEMA:
        raise ValueError("launcher schema is not supported")
    if type(value["command"]) is not list:
        raise ValueError("launcher command must be one exact JSON array")
    if type(value["image_environment"]) is not list:
        raise ValueError("launcher image_environment must be one exact JSON array")
    return HermeticLauncher(
        container_image_ref=value["container_image_ref"],
        command=tuple(value["command"]),
        inference_runtime_sha256=value["inference_runtime_sha256"],
        model_weights_sha256=value["model_weights_sha256"],
        tokenizer_sha256=value["tokenizer_sha256"],
        exact_model_version=value["exact_model_version"],
        model_provider=value["model_provider"],
        generation_settings=value["generation_settings"],
        image_environment=tuple(value["image_environment"]),
        container_user=value["container_user"],
        memory_bytes=value["memory_bytes"],
        nano_cpus=value["nano_cpus"],
        timeout_seconds=value["timeout_seconds"],
        max_output_bytes=value["max_output_bytes"],
    )


def load_capacity_binding_file(
    path: Path,
    launcher: HermeticLauncher,
    *,
    runtime: Mapping[str, Any],
    protocol: Mapping[str, Any],
) -> dict[str, Any]:
    """Load and cross-check one exact capacity allocation."""

    if type(launcher) is not HermeticLauncher:
        raise TypeError("launcher must be exact HermeticLauncher")
    if type(runtime) is not dict or type(protocol) is not dict:
        raise TypeError("runtime and protocol must be exact dictionaries")
    value = _load_exact_json_object(path, "capacity binding")
    if set(value) != _CAPACITY_BINDING_FIELDS:
        raise ValueError("capacity binding fields differ from the exact schema")
    if value["schema"] != CAPACITY_BINDING_SCHEMA:
        raise ValueError("capacity binding schema is not supported")
    _token(value["pool_id"], "capacity binding pool_id")
    if value["platform"] != "linux/amd64":
        raise ValueError("capacity binding platform must be linux/amd64")
    if type(value["pool_instance_count"]) is not int or value["pool_instance_count"] != 1:
        raise ValueError("capacity binding must allocate exactly one pool instance")
    if value["executor_image_ref"] != launcher.container_image_ref:
        raise ValueError("capacity binding image differs from the fixed launcher")
    if value["launcher_artifact_sha256"] != launcher.launcher_artifact_sha256:
        raise ValueError("capacity binding launcher digest differs from the fixed launcher")

    model_slot = value["model_slot"]
    if type(model_slot) is not dict or set(model_slot) != _MODEL_SLOT_FIELDS:
        raise ValueError("capacity model_slot fields differ from the exact schema")
    expected_model_slot = {
        "gpu_device_requests": 0,
        "max_output_bytes": launcher.max_output_bytes,
        "memory_bytes": launcher.memory_bytes,
        "nano_cpus": launcher.nano_cpus,
        "network": "none",
        "pids_limit": 256,
        "runtime": "runc",
        "timeout_seconds": launcher.timeout_seconds,
    }
    if model_slot != expected_model_slot:
        raise ValueError("capacity model_slot differs from the fixed launcher boundary")

    resource_limits = runtime.get("resource_limits")
    if type(resource_limits) is not dict:
        raise ValueError("runtime resource_limits must be one exact object")
    verifier_slot = value["verifier_slot"]
    if type(verifier_slot) is not dict or set(verifier_slot) != _VERIFIER_SLOT_FIELDS:
        raise ValueError("capacity verifier_slot fields differ from the exact schema")
    expected_verifier_slot = {
        key: resource_limits.get(key) for key in sorted(_VERIFIER_SLOT_FIELDS)
    }
    if verifier_slot != expected_verifier_slot:
        raise ValueError("capacity verifier_slot differs from the frozen runtime")

    concurrency = value["concurrency"]
    if type(concurrency) is not dict or set(concurrency) != _CONCURRENCY_FIELDS:
        raise ValueError("capacity concurrency fields differ from the exact schema")
    schedule = protocol.get("sealed_rules", {}).get("deterministic_schedule", {})
    expected_concurrency = {
        "max_model_dispatches": 1,
        "max_verifier_processes": 1,
        "protocol_rule": schedule.get("concurrency"),
    }
    if concurrency != expected_concurrency:
        raise ValueError("capacity concurrency differs from the sealed protocol")
    if value["selection_after_manifest"] != "BLOCKED":
        raise ValueError("capacity selection_after_manifest must remain BLOCKED")
    return json.loads(json.dumps(value, allow_nan=False, sort_keys=True))


def load_repository_execution_bindings(
    repository_root: Path,
) -> tuple[HermeticLauncher, dict[str, Any]]:
    """Load the only launcher/capacity pair admissible for this checkout."""

    if not isinstance(repository_root, Path):
        raise TypeError("repository_root must be exact pathlib.Path")
    root = repository_root.resolve(strict=True)
    launcher = load_launcher_file(root / LAUNCHER_RELATIVE_PATH)
    build_lock = _load_exact_json_object(root / BUILD_LOCK_RELATIVE_PATH, "build lock")
    if (
        build_lock.get("schema") != "supernova.hermetic-executor-build-lock.v1"
        or build_lock.get("platform") != "linux/amd64"
    ):
        raise ValueError("executor build lock schema or platform changed")
    model = build_lock.get("model")
    if type(model) is not dict:
        raise ValueError("executor build-lock model must be one exact object")
    if model.get("tokenizer_binding") != "EMBEDDED_IN_GGUF_BOUND_BY_FULL_FILE_SHA256":
        raise ValueError("executor tokenizer is not bound by the full model digest")

    publication = _load_exact_json_object(
        root / PUBLICATION_RELATIVE_PATH, "executor publication evidence"
    )
    if set(publication) != _PUBLICATION_FIELDS:
        raise ValueError("executor publication evidence fields differ from the exact schema")
    if publication["schema"] != "supernova.hermetic-executor-publication.v1":
        raise ValueError("executor publication evidence schema is not supported")
    if (
        publication["platform"] != "linux/amd64"
        or publication["publication_status"] != "PUBLISHED_IMMUTABLE"
        or type(publication["workflow_run_id"]) is not int
        or publication["workflow_run_id"] <= 0
    ):
        raise ValueError("executor publication evidence is not one immutable amd64 build")
    source_commit = _token(publication["source_commit"], "publication source_commit")
    if len(source_commit) != 40 or any(
        char not in "0123456789abcdef" for char in source_commit
    ):
        raise ValueError("publication source_commit must be one lowercase Git commit")
    if publication["workflow_url"] != (
        "https://github.com/Kitahl/Supernova-V2/actions/runs/"
        + str(publication["workflow_run_id"])
    ):
        raise ValueError("publication workflow URL does not bind its run id")
    artifact_digest = _token(
        publication["evidence_artifact_digest"], "publication artifact digest"
    )
    if not artifact_digest.startswith("sha256:"):
        raise ValueError("publication artifact digest must be sha256")
    _sha256_hex(artifact_digest[7:], "publication artifact digest")
    for field in (
        "build_lock_sha256",
        "executor_sha256",
        "llama_cli_sha256",
        "model_sha256",
    ):
        _sha256_hex(publication[field], f"publication {field}")
    image_digest = _token(publication["image_digest"], "publication image_digest")
    if not image_digest.startswith("sha256:"):
        raise ValueError("publication image_digest must be sha256")
    _sha256_hex(image_digest[7:], "publication image_digest")
    if publication["image_ref"] != (
        "ghcr.io/kitahl/supernova-goal1-executor@" + image_digest
    ):
        raise ValueError("publication image_ref differs from its digest")
    normalized_lock_bytes = (root / BUILD_LOCK_RELATIVE_PATH).read_bytes().replace(
        b"\r\n", b"\n"
    )
    if sha256(normalized_lock_bytes).hexdigest() != publication["build_lock_sha256"]:
        raise ValueError("executor publication evidence binds a different build lock")
    if (
        publication["image_ref"] != launcher.container_image_ref
        or publication["llama_cli_sha256"] != launcher.inference_runtime_sha256
        or publication["model_sha256"] != launcher.model_weights_sha256
        or publication["model_sha256"] != launcher.tokenizer_sha256
        or publication["model_sha256"] != model.get("sha256")
    ):
        raise ValueError("fixed launcher differs from published executor evidence")

    expected_launcher_values = {
        "command": list(launcher.command),
        "container_user": launcher.container_user,
        "exact_model_version": launcher.exact_model_version,
        "generation_settings": launcher.generation_settings,
        "image_environment": list(launcher.image_environment),
        "model_sha256": launcher.model_weights_sha256,
        "tokenizer_sha256": launcher.tokenizer_sha256,
    }
    locked_launcher_values = {
        "command": build_lock.get("command"),
        "container_user": build_lock.get("container_user"),
        "exact_model_version": model.get("exact_version"),
        "generation_settings": build_lock.get("generation_settings"),
        "image_environment": build_lock.get("image_environment"),
        "model_sha256": model.get("sha256"),
        "tokenizer_sha256": model.get("sha256"),
    }
    if expected_launcher_values != locked_launcher_values:
        raise ValueError("fixed launcher differs from the reviewed executor build lock")
    if launcher.model_provider != "HERMETIC_LOCAL_MODEL":
        raise ValueError("fixed launcher model_provider is not the hermetic provider")
    runtime = _load_exact_json_object(root / RUNTIME_RELATIVE_PATH, "confirmatory runtime")
    protocol = _load_exact_json_object(
        root / (Path("goal1") / "CONFIRMATORY_PROTOCOL.json"),
        "confirmatory protocol",
    )
    capacity = load_capacity_binding_file(
        root / CAPACITY_BINDING_RELATIVE_PATH,
        launcher,
        runtime=runtime,
        protocol=protocol,
    )
    return launcher, capacity


def _canonical_public_bytes(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _write_public_activation_artifacts(
    sealed: SealedExecutionAuthority,
    trust_root_path: Path,
    authority_path: Path,
) -> None:
    """Publish both public artifacts without ever replacing an existing file."""

    if type(sealed) is not SealedExecutionAuthority:
        raise TypeError("sealed must be exact SealedExecutionAuthority")
    if not isinstance(trust_root_path, Path) or not isinstance(authority_path, Path):
        raise TypeError("activation output paths must be exact pathlib.Path values")
    if trust_root_path.resolve(strict=False) == authority_path.resolve(strict=False):
        raise ValueError("activation output paths must be distinct")
    for path in (trust_root_path, authority_path):
        if not path.parent.is_dir():
            raise FileNotFoundError(f"activation output directory is absent: {path.parent}")
        if path.exists():
            raise FileExistsError(f"refusing to overwrite activation artifact: {path}")

    # Authority is staged first; the trust root is the loader-visible commit marker.
    # An interruption before the final link therefore remains fail-closed.
    outputs = (
        (authority_path, _canonical_public_bytes(sealed.authority)),
        (trust_root_path, _canonical_public_bytes(sealed.trust_root)),
    )
    staged: list[Path] = []
    published: list[Path] = []
    try:
        for output, payload in outputs:
            temporary = output.with_name(
                f".{output.name}.{secrets.token_hex(16)}.tmp"
            )
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            staged.append(temporary)
        for (output, _payload), temporary in zip(outputs, staged, strict=True):
            os.link(temporary, output)
            published.append(output)
    except BaseException:
        for output in reversed(published):
            output.unlink(missing_ok=True)
        raise
    finally:
        for temporary in staged:
            temporary.unlink(missing_ok=True)


def provision_repository_execution_authority(
    root_key_path: Path,
    receipt_key_path: Path,
    *,
    authority_id: str,
    root_key_id: str,
    receipt_issuer_id: str,
    validator_id: str,
) -> SealedExecutionAuthority:
    """Preflight and seal only the fixed repository launcher/capacity pair."""

    for value, field in (
        (root_key_path, "root_key_path"),
        (receipt_key_path, "receipt_key_path"),
    ):
        if not isinstance(value, Path):
            raise TypeError(f"{field} must be pathlib.Path")
    root = _repository_root().resolve(strict=True)
    resolved_keys: dict[str, Path] = {}
    for key_path, field in (
        (root_key_path, "root_key_path"),
        (receipt_key_path, "receipt_key_path"),
    ):
        resolved = key_path.resolve(strict=True)
        if resolved == root or root in resolved.parents:
            raise PermissionError(f"{field} must be stored outside the repository")
        resolved_keys[field] = resolved
    if (
        resolved_keys["root_key_path"] == resolved_keys["receipt_key_path"]
        or root_key_path.samefile(receipt_key_path)
    ):
        raise PermissionError("root and receipt key files must be distinct")

    goal_directory = root / "goal1"
    trust_root_path = goal_directory / "CONFIRMATORY_TRUST_ROOT.json"
    authority_path = goal_directory / "CONFIRMATORY_EXECUTION_AUTHORITY.json"
    for path in (trust_root_path, authority_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite activation artifact: {path}")

    launcher, capacity = load_repository_execution_bindings(root)
    protocol = _load_exact_json_object(
        goal_directory / "CONFIRMATORY_PROTOCOL.json", "confirmatory protocol"
    )
    goal1 = _load_exact_json_object(goal_directory / "GOAL1.json", "Goal-1 authority")

    receipt_private_key = load_private_key_file(receipt_key_path)
    preflight = run_hermetic_preflight(
        launcher,
        issuer_id=receipt_issuer_id,
        validator_id=validator_id,
        receipt_private_key=receipt_private_key,
    )
    root_private_key = load_private_key_file(root_key_path)
    if _public_bytes(_private_key(root_private_key, "root_private_key")) == _public_bytes(
        _private_key(receipt_private_key, "receipt_private_key")
    ):
        raise PermissionError("root and receipt signing identities must be distinct")
    sealed = _seal_execution_authority(
        protocol,
        goal1,
        launcher,
        preflight,
        authority_id=authority_id,
        root_key_id=root_key_id,
        root_private_key=root_private_key,
        receipt_issuer_id=receipt_issuer_id,
        pool_id=capacity["pool_id"],
        capacity_binding_sha256=canonical_sha256(capacity),
    )
    _write_public_activation_artifacts(sealed, trust_root_path, authority_path)
    try:
        capability = load_execution_authority(protocol, goal1)
        if (
            capability.authority_sha256 != canonical_sha256(sealed.authority)
            or capability.executor_artifact_sha256
            != launcher.executor_artifact_sha256
        ):
            raise ValueError("published authority reload produced a different capability")
    except BaseException:
        trust_root_path.unlink(missing_ok=True)
        authority_path.unlink(missing_ok=True)
        raise
    return sealed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run authentic hermetic preflight for the fixed checked-in launcher and "
            "capacity binding, then create public Goal-1 authority artifacts. "
            "Existing artifacts are never overwritten."
        )
    )
    parser.add_argument("--root-key", type=Path, required=True)
    parser.add_argument("--receipt-key", type=Path, required=True)
    parser.add_argument("--authority-id", required=True)
    parser.add_argument("--root-key-id", required=True)
    parser.add_argument("--receipt-issuer-id", required=True)
    parser.add_argument("--validator-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        sealed = provision_repository_execution_authority(
            args.root_key,
            args.receipt_key,
            authority_id=args.authority_id,
            root_key_id=args.root_key_id,
            receipt_issuer_id=args.receipt_issuer_id,
            validator_id=args.validator_id,
        )
    except Exception as exc:
        print(f"activation refused: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "authority_sha256": canonical_sha256(sealed.authority),
                "status": "PUBLIC_EXECUTION_AUTHORITY_CREATED",
                "trust_root_sha256": canonical_sha256(sealed.trust_root),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


__all__ = [
    "EMPTY_CONTEXT_SHA256",
    "HermeticLauncher",
    "PreflightEvidence",
    "SealedExecutionAuthority",
    "SupervisedAttempt",
    "SupervisorError",
    "load_capacity_binding_file",
    "load_launcher_file",
    "load_private_key_file",
    "load_repository_execution_bindings",
    "main",
    "provision_execution_authority",
    "provision_repository_execution_authority",
    "run_hermetic_preflight",
    "run_supervised_attempt",
]


if __name__ == "__main__":
    raise SystemExit(main())
