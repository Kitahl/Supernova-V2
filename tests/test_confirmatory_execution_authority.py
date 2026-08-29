from __future__ import annotations

from base64 import b64decode, b64encode
import copy
import inspect
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from supernova_goal1.activation import (
    _open_operational_gate,
    activate_confirmatory_execution,
)
from supernova_goal1.evidence_bridge import HermeticContextReceipt

from supernova_goal1.confirmatory_manifest import (
    assert_dispatch_authorized,
    build_confirmatory_manifest,
)
from supernova_goal1.execution_authority import (
    AUTHORIZED_DISPATCH_STATUS,
    AUTHORITY_SCHEMA,
    HERMETIC_CONTEXT_MODE,
    PRODUCTION_CREDIT_STATUS,
    PRODUCTION_RECEIPT_SCHEMA,
    ValidatedExecutionAuthority,
    _issue_validated_authority,
    _repository_root,
    _validate_authority_artifact,
    canonical_sha256,
    load_execution_authority,
    signed_bytes,
)


import supernova_goal1.execution_authority as execution_authority

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = json.loads(
    (ROOT / "goal1" / "CONFIRMATORY_PROTOCOL.json").read_text(encoding="utf-8")
)
GOAL1 = json.loads((ROOT / "goal1" / "GOAL1.json").read_text(encoding="utf-8"))


def _b64(value: bytes) -> str:
    return b64encode(value).decode("ascii")


def _public_bytes(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes_raw()


def _signed_fixture() -> tuple[dict[str, object], Ed25519PrivateKey, Ed25519PrivateKey]:
    root_private = Ed25519PrivateKey.generate()
    receipt_private = Ed25519PrivateKey.generate()
    receipt_public = _public_bytes(receipt_private)
    settings = {
        "max_output_tokens": 4096,
        "sampling": "GREEDY",
        "temperature": 0,
    }
    settings_sha = canonical_sha256(settings)
    model_identity_sha = canonical_sha256(
        {
            "exact_model_version": "fixture-model-v1@sha256:" + "1" * 64,
            "generation_settings_sha256": settings_sha,
            "model_provider": "HERMETIC_FIXTURE_PROVIDER",
        }
    )
    executor = {
        "container_image_digest": "sha256:" + "2" * 64,
        "fresh_process_per_attempt": True,
        "inference_runtime_sha256": "3" * 64,
        "launcher_artifact_sha256": "4" * 64,
        "model_weights_sha256": "5" * 64,
        "network_policy": "NONE",
        "persistent_writable_state": "DISABLED",
        "raw_credentials_exposed_to_child": False,
        "tokenizer_sha256": "6" * 64,
    }
    executor_sha = canonical_sha256(executor)
    preflight_body = {
        "clean_image_sha256": "7" * 64,
        "closed_at": "2026-08-28T00:01:00Z",
        "context_mode": HERMETIC_CONTEXT_MODE,
        "executor_artifact_sha256": executor_sha,
        "fresh_process_observed": True,
        "instance_nonce": "preflight-fixture-nonce",
        "issuer_id": "fixture-hermetic-supervisor",
        "model_identity_sha256": model_identity_sha,
        "network_policy": "NONE",
        "opened_at": "2026-08-28T00:00:00Z",
        "persistent_writable_state": "DISABLED",
        "schema": "supernova.hermetic-preflight-receipt.v1",
        "teardown_observed": True,
    }
    preflight = dict(preflight_body)
    preflight["signature"] = _b64(
        receipt_private.sign(
            signed_bytes("supernova.hermetic-preflight-receipt.v1", preflight_body)
        )
    )
    preflight_sha = canonical_sha256(preflight)
    validation = {
        "checks": [
            "CLEAN_IMAGE_MATCH",
            "FRESH_PROCESS_OBSERVED",
            "NETWORK_DISABLED",
            "NO_PERSISTENT_WRITABLE_STATE",
            "MODEL_IDENTITY_MATCH",
            "EXECUTOR_ARTIFACT_MATCH",
            "RECEIPT_SIGNATURE_VALID",
            "TEARDOWN_OBSERVED",
        ],
        "receipt_sha256": preflight_sha,
        "schema": "supernova.preflight-validation-record.v1",
        "validated_at": "2026-08-28T00:01:01Z",
        "validator_id": "fixture-host-validator",
        "verdict": "PASS",
    }
    schedule = copy.deepcopy(PROTOCOL["sealed_rules"]["deterministic_schedule"])
    pool = {
        "capacity_binding_sha256": "8" * 64,
        "policy": "PINNED_SINGLE_HERMETIC_POOL",
        "pool_id": "fixture-pool-v1",
        "selection_after_manifest": "BLOCKED",
    }
    body = {
        "authority_id": "fixture-authority-v1",
        "context_mode": HERMETIC_CONTEXT_MODE,
        "exact_model_version": "fixture-model-v1@sha256:" + "1" * 64,
        "executor_artifact": executor,
        "executor_artifact_sha256": executor_sha,
        "generation_settings": settings,
        "generation_settings_sha256": settings_sha,
        "goal1_authority_sha256": canonical_sha256(GOAL1),
        "model_provider": "HERMETIC_FIXTURE_PROVIDER",
        "preflight_receipt": preflight,
        "preflight_receipt_sha256": preflight_sha,
        "preflight_validation_record": validation,
        "preflight_validation_record_sha256": canonical_sha256(validation),
        "protocol_rules_sha256": PROTOCOL["sealed_rules_sha256"],
        "provider_attested_fresh_empty_context_capability": False,
        "receipt_issuer_id": "fixture-hermetic-supervisor",
        "receipt_schema": PRODUCTION_RECEIPT_SCHEMA,
        "receipt_verification_key_sha256": __import__("hashlib").sha256(
            receipt_public
        ).hexdigest(),
        "receipt_verification_public_key_b64": _b64(receipt_public),
        "root_key_id": "fixture-root-v1",
        "scheduling_policy": schedule,
        "scheduling_policy_sha256": canonical_sha256(schedule),
        "schema": AUTHORITY_SCHEMA,
        "serving_pool_policy": pool,
        "serving_pool_policy_sha256": canonical_sha256(pool),
    }
    authority = dict(body)
    authority["signature"] = _b64(root_private.sign(signed_bytes(AUTHORITY_SCHEMA, body)))
    return authority, root_private, receipt_private


class ConfirmatoryExecutionAuthorityTests(unittest.TestCase):
    def test_installed_package_without_fixed_checkout_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake_module = (
                Path(directory)
                / "site-packages"
                / "supernova_goal1"
                / "execution_authority.py"
            )
            with patch.object(execution_authority, "__file__", str(fake_module)):
                with self.assertRaisesRegex(
                    PermissionError, "BLOCKED_NO_FIXED_REPOSITORY_CHECKOUT"
                ):
                    _repository_root()

    def test_valid_signed_hermetic_bundle_is_immutable_capability(self) -> None:
        authority, root_private, receipt_private = _signed_fixture()
        validated = _validate_authority_artifact(
            authority,
            protocol=PROTOCOL,
            goal1=GOAL1,
            root_key_id="fixture-root-v1",
            root_public_key=_public_bytes(root_private),
        )
        self.assertEqual(canonical_sha256(authority), validated.authority_sha256)
        self.assertEqual(_public_bytes(receipt_private), validated.receipt_public_key)
        self.assertEqual(HERMETIC_CONTEXT_MODE, validated.context_mode)

    def test_capability_cannot_be_constructed_by_a_caller(self) -> None:
        self.assertFalse(hasattr(execution_authority, "_AUTHORITY_FACTORY"))
        with self.assertRaisesRegex(TypeError, "fixed repository loader"):
            ValidatedExecutionAuthority()
    def test_public_loader_has_no_authority_or_key_argument_and_missing_file_blocks(self) -> None:
        self.assertEqual(["protocol", "goal1"], list(inspect.signature(load_execution_authority).parameters))
        self.assertEqual(
            ["protocol", "goal1", "operator_seed"],
            list(inspect.signature(activate_confirmatory_execution).parameters),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            goal = root / "goal1"
            goal.mkdir()
            for name in (
                "CONFIRMATORY_PROTOCOL.json",
                "CONFIRMATORY_TRUST_ROOT.json",
                "GOAL1.json",
            ):
                (goal / name).write_bytes((ROOT / "goal1" / name).read_bytes())

            with patch.object(
                execution_authority,
                "_repository_root",
                return_value=root,
            ):
                with self.assertRaisesRegex(
                    PermissionError, "BLOCKED_NO_EXECUTION_AUTHORITY"
                ):
                    load_execution_authority(PROTOCOL, GOAL1)
                with self.assertRaisesRegex(
                    PermissionError, "BLOCKED_NO_EXECUTION_AUTHORITY"
                ):
                    activate_confirmatory_execution(
                        PROTOCOL,
                        GOAL1,
                        operator_seed=b"x" * 32,
                    )

    def test_fixed_repository_authority_activates(self) -> None:
        capability = load_execution_authority(PROTOCOL, GOAL1)
        activated = activate_confirmatory_execution(
            PROTOCOL,
            GOAL1,
            operator_seed=b"B" * 32,
        )
        self.assertEqual(capability, activated.authority)
        self.assertEqual(
            AUTHORIZED_DISPATCH_STATUS,
            activated.protocol["confirmatory_execution_status"],
        )
        self.assertEqual(
            PRODUCTION_CREDIT_STATUS,
            activated.manifest.public_manifest["credit_status"],
        )

    def test_random_self_selected_root_cannot_activate(self) -> None:
        authority, _, _ = _signed_fixture()
        unrelated_root = Ed25519PrivateKey.generate()
        with self.assertRaisesRegex(ValueError, "signature"):
            _validate_authority_artifact(
                authority,
                protocol=PROTOCOL,
                goal1=GOAL1,
                root_key_id="fixture-root-v1",
                root_public_key=_public_bytes(unrelated_root),
            )

    def test_mutated_binding_and_nonhermetic_executor_are_rejected(self) -> None:
        authority, root_private, _ = _signed_fixture()
        changed = copy.deepcopy(authority)
        changed["generation_settings"]["temperature"] = 1
        with self.assertRaisesRegex(ValueError, "generation_settings_sha256"):
            _validate_authority_artifact(
                changed, protocol=PROTOCOL, goal1=GOAL1,
                root_key_id="fixture-root-v1", root_public_key=_public_bytes(root_private),
            )
        changed = copy.deepcopy(authority)
        changed["executor_artifact"]["network_policy"] = "DEFAULT"
        changed["executor_artifact_sha256"] = canonical_sha256(changed["executor_artifact"])
        with self.assertRaisesRegex(ValueError, "hermetic boundary"):
            _validate_authority_artifact(
                changed, protocol=PROTOCOL, goal1=GOAL1,
                root_key_id="fixture-root-v1", root_public_key=_public_bytes(root_private),
            )

    def test_simulation_chat_and_provider_aliases_remain_noncredit(self) -> None:
        authority, root_private, _ = _signed_fixture()
        for issuer in ("NON_CREDIT_SIMULATION", "scheduled-chat-worker"):
            changed = copy.deepcopy(authority)
            changed["receipt_issuer_id"] = issuer
            with self.subTest(issuer=issuer), self.assertRaisesRegex(ValueError, "non-credit"):
                _validate_authority_artifact(
                    changed, protocol=PROTOCOL, goal1=GOAL1,
                    root_key_id="fixture-root-v1", root_public_key=_public_bytes(root_private),
                )
        changed = copy.deepcopy(authority)
        changed["provider_attested_fresh_empty_context_capability"] = True
        with self.assertRaisesRegex(ValueError, "must not claim provider attestation"):
            _validate_authority_artifact(
                changed, protocol=PROTOCOL, goal1=GOAL1,
                root_key_id="fixture-root-v1", root_public_key=_public_bytes(root_private),
            )

    def test_schedule_pool_goal_and_preflight_drift_are_rejected(self) -> None:
        authority, root_private, _ = _signed_fixture()
        cases = []
        changed = copy.deepcopy(authority)
        changed["scheduling_policy"]["attempt_order_per_arm"] = "RANDOM"
        cases.append((changed, "scheduling policy"))
        changed = copy.deepcopy(authority)
        changed["serving_pool_policy"]["selection_after_manifest"] = "ALLOWED"
        changed["serving_pool_policy_sha256"] = canonical_sha256(changed["serving_pool_policy"])
        cases.append((changed, "prospectively pinned"))
        changed = copy.deepcopy(authority)
        changed["goal1_authority_sha256"] = "0" * 64
        cases.append((changed, "GOAL1"))
        changed = copy.deepcopy(authority)
        changed["preflight_receipt"]["teardown_observed"] = False
        changed["preflight_receipt_sha256"] = canonical_sha256(changed["preflight_receipt"])
        cases.append((changed, "hermetic boundary"))
        for changed, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                _validate_authority_artifact(
                    changed, protocol=PROTOCOL, goal1=GOAL1,
                    root_key_id="fixture-root-v1", root_public_key=_public_bytes(root_private),
                )

    def test_hermetic_receipt_signature_binds_every_execution_observation(self) -> None:
        authority, root_private, receipt_private = _signed_fixture()
        validated = _validate_authority_artifact(
            authority,
            protocol=PROTOCOL,
            goal1=GOAL1,
            root_key_id="fixture-root-v1",
            root_public_key=_public_bytes(root_private),
        )
        body = {
            "arm": "ordinary",
            "attempt_index": 0,
            "clean_image_sha256": validated.clean_image_sha256,
            "closed_at": "2026-08-28T00:02:00Z",
            "confirmatory_manifest_sha256": "9" * 64,
            "dispatch_id": "a" * 64,
            "execution_authority_sha256": validated.authority_sha256,
            "executor_artifact_sha256": validated.executor_artifact_sha256,
            "initial_context_sha256": __import__("hashlib").sha256(b"").hexdigest(),
            "instance_nonce": "fresh-instance-0001",
            "issuer_id": validated.issuer_id,
            "model_identity_sha256": validated.model_identity_sha256,
            "network_policy": "NONE",
            "opened_at": "2026-08-28T00:01:00Z",
            "persistent_writable_state": "DISABLED",
            "problem_id": "fixture-problem",
            "protocol_dispatch_id": "dispatch-" + "b" * 64,
            "request_artifact_sha256": "c" * 64,
            "response_artifact_sha256": "d" * 64,
            "run_id": "fixture-run",
            "schema": PRODUCTION_RECEIPT_SCHEMA,
            "sequence": 0,
            "teardown_observed": True,
        }
        signature = _b64(
            receipt_private.sign(signed_bytes(PRODUCTION_RECEIPT_SCHEMA, body))
        )
        receipt = HermeticContextReceipt(
            issuer_id=body["issuer_id"],
            execution_authority_sha256=body["execution_authority_sha256"],
            confirmatory_manifest_sha256=body["confirmatory_manifest_sha256"],
            model_identity_sha256=body["model_identity_sha256"],
            executor_artifact_sha256=body["executor_artifact_sha256"],
            run_id=body["run_id"], protocol_dispatch_id=body["protocol_dispatch_id"],
            dispatch_id=body["dispatch_id"], problem_id=body["problem_id"],
            arm=body["arm"], attempt_index=body["attempt_index"],
            sequence=body["sequence"], instance_nonce=body["instance_nonce"],
            clean_image_sha256=body["clean_image_sha256"],
            initial_context_sha256=body["initial_context_sha256"],
            request_artifact_sha256=body["request_artifact_sha256"],
            response_artifact_sha256=body["response_artifact_sha256"],
            opened_at=body["opened_at"], closed_at=body["closed_at"],
            network_policy=body["network_policy"],
            persistent_writable_state=body["persistent_writable_state"],
            teardown_observed=body["teardown_observed"], signature=signature,
        )
        self.assertEqual(body, receipt.body())
        Ed25519PublicKey.from_public_bytes(validated.receipt_public_key).verify(
            b64decode(receipt.signature),
            signed_bytes(PRODUCTION_RECEIPT_SCHEMA, receipt.body()),
        )

    def test_validation_returns_details_not_a_capability(self) -> None:
        authority, root_private, _ = _signed_fixture()
        details = _validate_authority_artifact(
            authority,
            protocol=PROTOCOL,
            goal1=GOAL1,
            root_key_id="fixture-root-v1",
            root_public_key=_public_bytes(root_private),
        )
        self.assertIsNot(type(details), ValidatedExecutionAuthority)
        self.assertEqual(canonical_sha256(authority), details.authority_sha256)


    def test_fixed_loader_activation_opens_only_exact_operational_fields(self) -> None:
        artifact, root_private, _ = _signed_fixture()
        validation = _validate_authority_artifact(
            artifact,
            protocol=PROTOCOL,
            goal1=GOAL1,
            root_key_id="fixture-root-v1",
            root_public_key=_public_bytes(root_private),
        )
        capability = _issue_validated_authority(validation)
        with patch(
            "supernova_goal1.confirmatory_manifest.load_execution_authority",
            return_value=capability,
        ) as fixed_loader:
            activated = activate_confirmatory_execution(
                PROTOCOL,
                GOAL1,
                operator_seed=bytes.fromhex("9f" * 32),
            )
        fixed_loader.assert_called_once_with(PROTOCOL, GOAL1)

        expected = copy.deepcopy(PROTOCOL)
        expected["confirmatory_execution_status"] = AUTHORIZED_DISPATCH_STATUS
        expected["execution_opening_gate"]["state"] = AUTHORIZED_DISPATCH_STATUS
        expected["execution_opening_gate"]["missing_artifact"] = None
        self.assertEqual(expected, activated.protocol)
        self.assertEqual(PROTOCOL["sealed_rules"], activated.protocol["sealed_rules"])
        self.assertEqual(
            PROTOCOL["sealed_rules_sha256"],
            activated.protocol["sealed_rules_sha256"],
        )
        self.assertEqual(
            PRODUCTION_CREDIT_STATUS,
            activated.manifest.public_manifest["credit_status"],
        )
        self.assertEqual(
            capability.authority_sha256,
            activated.manifest.public_manifest["bindings"]["execution_authority_sha256"],
        )
        self.assertEqual(
            capability.model_identity_sha256,
            activated.manifest.public_manifest["bindings"]["model_identity_sha256"],
        )
        assert_dispatch_authorized(
            activated.manifest.public_manifest,
            activated.manifest.operator_plan,
            activated.protocol,
            execution_authority=capability,
        )

        relabeled = copy.deepcopy(activated.manifest.public_manifest)
        relabeled["bindings"]["execution_authority_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "authorized reconstruction"):
            assert_dispatch_authorized(
                relabeled,
                activated.manifest.operator_plan,
                activated.protocol,
                execution_authority=capability,
            )

    def test_public_manifest_builder_rejects_caller_injected_real_capability(self) -> None:
        artifact, root_private, _ = _signed_fixture()
        validation = _validate_authority_artifact(
            artifact,
            protocol=PROTOCOL,
            goal1=GOAL1,
            root_key_id="fixture-root-v1",
            root_public_key=_public_bytes(root_private),
        )
        capability = _issue_validated_authority(validation)
        opened = _open_operational_gate(PROTOCOL)
        for candidate in (PROTOCOL, opened):
            with self.subTest(status=candidate["confirmatory_execution_status"]):
                with self.assertRaisesRegex(PermissionError, "activation-only"):
                    build_confirmatory_manifest(
                        candidate,
                        operator_seed=bytes.fromhex("9f" * 32),
                        execution_authority=capability,
                    )

    def test_fixed_root_ignores_caller_environment_key_material(self) -> None:
        trusted = Ed25519PrivateKey.generate()
        attacker = Ed25519PrivateKey.generate()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            goal = root / "goal1"
            goal.mkdir()
            (goal / "CONFIRMATORY_TRUST_ROOT.json").write_text(
                json.dumps(
                    {
                        "schema": "supernova.confirmatory-trust-root.v1",
                        "root_key_id": "trusted-root",
                        "ed25519_public_key_b64": _b64(_public_bytes(trusted)),
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "SUPERNOVA_GOAL1_ROOT_KEY_ID": "attacker-root",
                    "SUPERNOVA_GOAL1_ROOT_PUBLIC_KEY_B64": _b64(_public_bytes(attacker)),
                },
                clear=False,
            ):
                key_id, key = execution_authority._fixed_root(root)
        self.assertEqual("trusted-root", key_id)
        self.assertEqual(_public_bytes(trusted), key)
    def test_activation_rejects_every_nonoperational_protocol_mutation(self) -> None:
        cases = []

        changed = copy.deepcopy(PROTOCOL)
        changed["scientific_credit"] = "PASS_ALREADY"
        cases.append(("scientific_credit", changed))

        changed = copy.deepcopy(PROTOCOL)
        changed["protocol_id"] = "attacker-protocol"
        cases.append(("protocol_id", changed))

        changed = copy.deepcopy(PROTOCOL)
        changed["mutation_policy"] = {"sealed_rules_or_digest_change": "ALLOW"}
        cases.append(("mutation_policy", changed))

        changed = copy.deepcopy(PROTOCOL)
        changed["execution_opening_gate"]["transition"] = "CALLER_CONTROLLED"
        cases.append(("opening_transition", changed))

        changed = copy.deepcopy(PROTOCOL)
        changed["caller_extension"] = "UNRECOGNIZED"
        cases.append(("extra_field", changed))

        for field, changed in cases:
            with self.subTest(field=field), self.assertRaisesRegex(
                PermissionError, "exact checked-in frozen protocol"
            ):
                activate_confirmatory_execution(
                    changed,
                    GOAL1,
                    operator_seed=bytes.fromhex("9f" * 32),
                )


if __name__ == "__main__":
    unittest.main()
