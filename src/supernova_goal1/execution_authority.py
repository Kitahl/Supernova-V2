from __future__ import annotations

from base64 import b64decode, b64encode
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import unicodedata
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


AUTHORITY_SCHEMA = "supernova.confirmatory-execution-authority.v1"
HERMETIC_CONTEXT_MODE = "HERMETIC_LOCAL_INSTANCE"
PRODUCTION_RECEIPT_SCHEMA = "supernova.hermetic-context-receipt.v1"
PRODUCTION_CREDIT_STATUS = "PRODUCTION_CREDIT_ELIGIBLE"
PRODUCTION_BRIDGE_RECEIPT_SCHEMA = "supernova.hermetic-evidence-bridge-receipt.v1"
AUTHORIZED_DISPATCH_STATUS = "AUTHORIZED_BY_VALIDATED_EXECUTION_AUTHORITY"
AUTHORITY_RELATIVE_PATH = Path("goal1") / "CONFIRMATORY_EXECUTION_AUTHORITY.json"
TRUST_ROOT_RELATIVE_PATH = Path("goal1") / "CONFIRMATORY_TRUST_ROOT.json"

PROTOCOL_RELATIVE_PATH = Path("goal1") / "CONFIRMATORY_PROTOCOL.json"
GOAL1_RELATIVE_PATH = Path("goal1") / "GOAL1.json"
_TRUST_ROOT_FIELDS = {"ed25519_public_key_b64", "root_key_id", "schema"}
_AUTHORITY_FIELDS = {
    "authority_id", "context_mode", "exact_model_version", "executor_artifact",
    "executor_artifact_sha256", "generation_settings", "generation_settings_sha256",
    "goal1_authority_sha256", "model_provider", "preflight_receipt",
    "preflight_receipt_sha256", "preflight_validation_record",
    "preflight_validation_record_sha256", "protocol_rules_sha256",
    "provider_attested_fresh_empty_context_capability", "receipt_issuer_id",
    "receipt_schema", "receipt_verification_key_sha256",
    "receipt_verification_public_key_b64", "root_key_id", "scheduling_policy",
    "scheduling_policy_sha256", "schema", "serving_pool_policy",
    "serving_pool_policy_sha256", "signature",
}
_EXECUTOR_FIELDS = {
    "container_image_digest", "fresh_process_per_attempt",
    "inference_runtime_sha256", "launcher_artifact_sha256", "model_weights_sha256",
    "network_policy", "persistent_writable_state", "raw_credentials_exposed_to_child",
    "tokenizer_sha256",
}
_PREFLIGHT_FIELDS = {
    "clean_image_sha256", "closed_at", "context_mode", "executor_artifact_sha256",
    "fresh_process_observed", "instance_nonce", "issuer_id", "model_identity_sha256",
    "network_policy", "opened_at", "persistent_writable_state", "schema", "signature",
    "teardown_observed",
}
_VALIDATION_FIELDS = {
    "checks", "receipt_sha256", "schema", "validated_at", "validator_id", "verdict",
}
_PREFLIGHT_CHECKS = [
    "CLEAN_IMAGE_MATCH", "FRESH_PROCESS_OBSERVED", "NETWORK_DISABLED",
    "NO_PERSISTENT_WRITABLE_STATE", "MODEL_IDENTITY_MATCH",
    "EXECUTOR_ARTIFACT_MATCH", "RECEIPT_SIGNATURE_VALID", "TEARDOWN_OBSERVED",
]


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


def _token(value: object, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field} must be an exact non-empty trimmed string")
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in value):
        raise ValueError(f"{field} must not contain control or format characters")
    value.encode("utf-8")
    return value


def _sha256_hex(value: object, field: str) -> str:
    value = _token(value, field)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field} must be exactly 64 lowercase hexadecimal characters")
    return value


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{field} must be an exact object")
    return value


def _exact_fields(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ValueError(f"{field} fields changed; missing={missing}, extra={extra}")


def _decode_public_key(value: object, field: str) -> bytes:
    value = _token(value, field)
    try:
        decoded = b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError(f"{field} must be canonical base64") from exc
    if len(decoded) != 32 or b64encode(decoded).decode("ascii") != value:
        raise ValueError(f"{field} must encode exactly one raw Ed25519 public key")
    return decoded


def _decode_signature(value: object, field: str) -> bytes:
    value = _token(value, field)
    try:
        decoded = b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError(f"{field} must be canonical base64") from exc
    if len(decoded) != 64 or b64encode(decoded).decode("ascii") != value:
        raise ValueError(f"{field} must encode exactly one Ed25519 signature")
    return decoded


def signed_bytes(domain: str, body: Mapping[str, Any]) -> bytes:
    domain_bytes = domain.encode("ascii")
    return len(domain_bytes).to_bytes(4, "big") + domain_bytes + canonical_bytes(body)


def _without_signature(value: Mapping[str, Any]) -> dict[str, object]:
    return {key: value[key] for key in sorted(value) if key != "signature"}


def _verify_signature(
    public_key: bytes, signature: object, *, domain: str,
    body: Mapping[str, Any], field: str,
) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            _decode_signature(signature, field), signed_bytes(domain, body)
        )
    except InvalidSignature as exc:
        raise ValueError(f"{field} is not valid for the bound artifact") from exc


@dataclass(frozen=True, init=False)
class ValidatedExecutionAuthority:
    authority_sha256: str
    authority_id: str
    context_mode: str
    clean_image_sha256: str
    executor_artifact_sha256: str
    issuer_id: str
    model_identity_sha256: str
    receipt_public_key: bytes
    receipt_schema: str
    root_key_id: str
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError(
            "ValidatedExecutionAuthority is issued only by the fixed repository loader"
        )

    def _validate_fields(self) -> None:
        _sha256_hex(self.authority_sha256, "authority_sha256")
        _token(self.authority_id, "authority_id")
        if self.context_mode != HERMETIC_CONTEXT_MODE:
            raise ValueError("unsupported production context mode")
        _sha256_hex(self.clean_image_sha256, "clean_image_sha256")
        _sha256_hex(self.executor_artifact_sha256, "executor_artifact_sha256")
        _token(self.issuer_id, "issuer_id")
        _sha256_hex(self.model_identity_sha256, "model_identity_sha256")
        if type(self.receipt_public_key) is not bytes or len(self.receipt_public_key) != 32:
            raise ValueError("receipt_public_key must be one raw Ed25519 public key")
        if self.receipt_schema != PRODUCTION_RECEIPT_SCHEMA:
            raise ValueError("unsupported production receipt schema")
        _token(self.root_key_id, "root_key_id")

    def verify_receipt_signature(
        self, signature: object, *, domain: str, body: Mapping[str, Any]
    ) -> None:
        """Verify a supervisor observation; this capability never signs receipts."""
        _verify_signature(
            self.receipt_public_key,
            signature,
            domain=domain,
            body=body,
            field="production context receipt signature",
        )


@dataclass(frozen=True)
class AuthorityValidation:
    authority_sha256: str
    authority_id: str
    context_mode: str
    clean_image_sha256: str
    executor_artifact_sha256: str
    issuer_id: str
    model_identity_sha256: str
    receipt_public_key: bytes
    receipt_schema: str
    root_key_id: str


def _issue_validated_authority(
    validation: AuthorityValidation,
) -> ValidatedExecutionAuthority:
    """The sole construction path after fixed-artifact validation succeeds."""

    if type(validation) is not AuthorityValidation:
        raise TypeError("validation must be exact AuthorityValidation")
    capability = object.__new__(ValidatedExecutionAuthority)
    for field in (
        "authority_sha256",
        "authority_id",
        "context_mode",
        "clean_image_sha256",
        "executor_artifact_sha256",
        "issuer_id",
        "model_identity_sha256",
        "receipt_public_key",
        "receipt_schema",
        "root_key_id",
    ):
        object.__setattr__(capability, field, getattr(validation, field))
    capability._validate_fields()
    return capability


def _fixed_root(repository_root: Path) -> tuple[str, bytes]:
    path = repository_root / TRUST_ROOT_RELATIVE_PATH
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PermissionError("BLOCKED_NO_EXECUTION_AUTHORITY: fixed trust root is absent") from exc
    root = _mapping(root, "execution trust root")
    _exact_fields(root, _TRUST_ROOT_FIELDS, "execution trust root")
    if root["schema"] != "supernova.confirmatory-trust-root.v1":
        raise ValueError("unsupported execution trust-root schema")
    return _token(root["root_key_id"], "root_key_id"), _decode_public_key(root["ed25519_public_key_b64"], "ed25519_public_key_b64")


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _model_identity(authority: Mapping[str, Any]) -> dict[str, object]:
    return {
        "exact_model_version": authority["exact_model_version"],
        "generation_settings_sha256": authority["generation_settings_sha256"],
        "model_provider": authority["model_provider"],
    }


def _validate_authority_artifact(
    authority: Mapping[str, Any], *, protocol: Mapping[str, Any],
    goal1: Mapping[str, Any], root_key_id: str, root_public_key: bytes,
) -> AuthorityValidation:
    authority = _mapping(authority, "execution authority")
    _exact_fields(authority, _AUTHORITY_FIELDS, "execution authority")
    if authority["schema"] != AUTHORITY_SCHEMA:
        raise ValueError("unsupported execution authority schema")
    if authority["context_mode"] != HERMETIC_CONTEXT_MODE:
        raise ValueError("only HERMETIC_LOCAL_INSTANCE can receive production credit")
    if authority["provider_attested_fresh_empty_context_capability"] is not False:
        raise ValueError("hermetic execution must not claim provider attestation")
    if authority["root_key_id"] != root_key_id:
        raise ValueError("execution authority root_key_id is not the host trust root")

    sealed_rules = _mapping(protocol.get("sealed_rules"), "sealed_rules")
    rules_sha = _sha256_hex(protocol.get("sealed_rules_sha256"), "sealed_rules_sha256")
    if canonical_sha256(sealed_rules) != rules_sha:
        raise ValueError("sealed protocol rules digest mismatch")
    if authority["protocol_rules_sha256"] != rules_sha:
        raise ValueError("execution authority binds a different protocol")
    if authority["goal1_authority_sha256"] != canonical_sha256(goal1):
        raise ValueError("execution authority binds different GOAL1 authority bytes")
    active = _mapping(goal1.get("active_experiment"), "goal1.active_experiment")
    if (
        goal1.get("schema_version") != 2
        or goal1.get("authority_id") != "goal1-active-authority-v2"
        or active.get("experiment_id") != "goal1-confirmatory-v1"
        or active.get("phase") != "CONFIRMATORY_PREEXECUTION"
        or active.get("protocol_rules_status") != "SEALED"
        or active.get("confirmatory_execution_status") != "BLOCKED_NO_EXECUTION_AUTHORITY"
        or active.get("benchmark_frozen") is not True
        or active.get("complete_cost_policy_frozen") is not True
        or active.get("held_out_dispatch") != "BLOCKED"
    ):
        raise ValueError("GOAL1 is not the exact frozen preexecution authority")

    settings = _mapping(authority["generation_settings"], "generation_settings")
    if canonical_sha256(settings) != authority["generation_settings_sha256"]:
        raise ValueError("generation_settings_sha256 mismatch")
    _token(authority["model_provider"], "model_provider")
    _token(authority["exact_model_version"], "exact_model_version")
    model_identity_sha256 = canonical_sha256(_model_identity(authority))

    executor = _mapping(authority["executor_artifact"], "executor_artifact")
    _exact_fields(executor, _EXECUTOR_FIELDS, "executor_artifact")
    if canonical_sha256(executor) != authority["executor_artifact_sha256"]:
        raise ValueError("executor_artifact_sha256 mismatch")
    for field in ("inference_runtime_sha256", "launcher_artifact_sha256",
                  "model_weights_sha256", "tokenizer_sha256"):
        _sha256_hex(executor[field], f"executor_artifact.{field}")
    image = _token(executor["container_image_digest"], "container_image_digest")
    if not image.startswith("sha256:"):
        raise ValueError("container_image_digest must be a sha256 digest")
    _sha256_hex(image[7:], "container_image_digest")
    if (
        executor["network_policy"] != "NONE"
        or executor["persistent_writable_state"] != "DISABLED"
        or executor["fresh_process_per_attempt"] is not True
        or executor["raw_credentials_exposed_to_child"] is not False
    ):
        raise ValueError("executor does not enforce the hermetic boundary")

    receipt_key = _decode_public_key(
        authority["receipt_verification_public_key_b64"],
        "receipt_verification_public_key_b64",
    )
    if sha256(receipt_key).hexdigest() != authority["receipt_verification_key_sha256"]:
        raise ValueError("receipt_verification_key_sha256 mismatch")
    if authority["receipt_schema"] != PRODUCTION_RECEIPT_SCHEMA:
        raise ValueError("unsupported production receipt schema")
    issuer_id = _token(authority["receipt_issuer_id"], "receipt_issuer_id")
    if "SIMULATION" in issuer_id.upper() or "CHAT" in issuer_id.upper():
        raise ValueError("simulation and recurring-chat issuers are non-credit only")

    preflight = _mapping(authority["preflight_receipt"], "preflight_receipt")
    _exact_fields(preflight, _PREFLIGHT_FIELDS, "preflight_receipt")
    if canonical_sha256(preflight) != authority["preflight_receipt_sha256"]:
        raise ValueError("preflight_receipt_sha256 mismatch")
    if (
        preflight["schema"] != "supernova.hermetic-preflight-receipt.v1"
        or preflight["issuer_id"] != issuer_id
        or preflight["context_mode"] != HERMETIC_CONTEXT_MODE
        or preflight["model_identity_sha256"] != model_identity_sha256
        or preflight["executor_artifact_sha256"] != authority["executor_artifact_sha256"]
        or preflight["network_policy"] != "NONE"
        or preflight["persistent_writable_state"] != "DISABLED"
        or preflight["fresh_process_observed"] is not True
        or preflight["teardown_observed"] is not True
    ):
        raise ValueError("preflight receipt does not prove the hermetic boundary")
    _sha256_hex(preflight["clean_image_sha256"], "preflight.clean_image_sha256")
    for field in ("instance_nonce", "opened_at", "closed_at"):
        _token(preflight[field], f"preflight.{field}")
    _verify_signature(
        receipt_key, preflight["signature"], domain="supernova.hermetic-preflight-receipt.v1",
        body=_without_signature(preflight), field="preflight_receipt.signature",
    )

    validation = _mapping(authority["preflight_validation_record"], "preflight_validation_record")
    _exact_fields(validation, _VALIDATION_FIELDS, "preflight_validation_record")
    if canonical_sha256(validation) != authority["preflight_validation_record_sha256"]:
        raise ValueError("preflight_validation_record_sha256 mismatch")
    if (
        validation["schema"] != "supernova.preflight-validation-record.v1"
        or validation["receipt_sha256"] != authority["preflight_receipt_sha256"]
        or validation["verdict"] != "PASS"
        or validation["checks"] != _PREFLIGHT_CHECKS
    ):
        raise ValueError("preflight validation record is not the complete PASS record")
    _token(validation["validator_id"], "preflight.validator_id")
    _token(validation["validated_at"], "preflight.validated_at")

    schedule = _mapping(authority["scheduling_policy"], "scheduling_policy")
    frozen_schedule = _mapping(sealed_rules.get("deterministic_schedule"), "deterministic_schedule")
    if schedule != frozen_schedule or canonical_sha256(schedule) != authority["scheduling_policy_sha256"]:
        raise ValueError("scheduling policy differs from the sealed schedule")
    pool = _mapping(authority["serving_pool_policy"], "serving_pool_policy")
    if canonical_sha256(pool) != authority["serving_pool_policy_sha256"]:
        raise ValueError("serving_pool_policy_sha256 mismatch")
    if set(pool) != {"capacity_binding_sha256", "pool_id", "policy", "selection_after_manifest"}:
        raise ValueError("serving_pool_policy fields changed")
    if pool["policy"] != "PINNED_SINGLE_HERMETIC_POOL" or pool["selection_after_manifest"] != "BLOCKED":
        raise ValueError("serving pool is not prospectively pinned")
    _token(pool["pool_id"], "serving_pool_policy.pool_id")
    _sha256_hex(pool["capacity_binding_sha256"], "serving_pool_policy.capacity_binding_sha256")

    _verify_signature(
        root_public_key, authority["signature"], domain=AUTHORITY_SCHEMA,
        body=_without_signature(authority), field="execution authority signature",
    )
    return AuthorityValidation(
        authority_sha256=canonical_sha256(authority),
        authority_id=_token(authority["authority_id"], "authority_id"),
        context_mode=HERMETIC_CONTEXT_MODE,
        clean_image_sha256=preflight["clean_image_sha256"],
        executor_artifact_sha256=authority["executor_artifact_sha256"],
        issuer_id=issuer_id,
        model_identity_sha256=model_identity_sha256,
        receipt_public_key=receipt_key,
        receipt_schema=PRODUCTION_RECEIPT_SCHEMA,
        root_key_id=root_key_id,
    )


def load_execution_authority(
    protocol: Mapping[str, Any], goal1: Mapping[str, Any]
) -> ValidatedExecutionAuthority:
    """Mint authority only from fixed repository artifacts and the fixed trust root."""
    repository_root = _repository_root()
    key_id, root_key = _fixed_root(repository_root)
    fixed_protocol = json.loads(
        (repository_root / PROTOCOL_RELATIVE_PATH).read_text(encoding="utf-8")
    )
    fixed_goal1 = json.loads(
        (repository_root / GOAL1_RELATIVE_PATH).read_text(encoding="utf-8")
    )
    if protocol != fixed_protocol or goal1 != fixed_goal1:
        raise PermissionError(
            "execution authority requires exact checked-in protocol and GOAL1 bytes"
        )
    path = repository_root / AUTHORITY_RELATIVE_PATH
    try:
        authority = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PermissionError(
            "BLOCKED_NO_EXECUTION_AUTHORITY: fixed authority artifact is absent"
        ) from exc
    validated = _validate_authority_artifact(
        authority, protocol=protocol, goal1=goal1,
        root_key_id=key_id, root_public_key=root_key,
    )
    return _issue_validated_authority(validated)


__all__ = [
    "AUTHORIZED_DISPATCH_STATUS", "AUTHORITY_RELATIVE_PATH", "AUTHORITY_SCHEMA",
    "HERMETIC_CONTEXT_MODE", "PRODUCTION_BRIDGE_RECEIPT_SCHEMA",
    "PRODUCTION_CREDIT_STATUS", "PRODUCTION_RECEIPT_SCHEMA",
    "TRUST_ROOT_RELATIVE_PATH", "ValidatedExecutionAuthority", "load_execution_authority",
]
