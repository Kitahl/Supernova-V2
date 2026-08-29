from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from supernova_goal1.confirmatory_supervisor import (
    EMPTY_CONTEXT_SHA256,
    HermeticLauncher,
    SupervisorError,
    provision_execution_authority,
    run_hermetic_preflight,
    run_supervised_attempt,
)
from supernova_goal1.execution_authority import (
    _issue_validated_authority,
    _validate_authority_artifact,
)


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = json.loads(
    (ROOT / "goal1" / "CONFIRMATORY_PROTOCOL.json").read_text(encoding="utf-8")
)
GOAL1 = json.loads((ROOT / "goal1" / "GOAL1.json").read_text(encoding="utf-8"))
IMAGE_ID = "sha256:" + "a" * 64
CONTAINER_ID = "b" * 64
TMPFS = {
    "/run": "rw,noexec,nosuid,size=16777216",
    "/tmp": "rw,noexec,nosuid,size=536870912",
}


def _raw_private(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _completed(
    argv: list[str], *, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def _launcher(settings: dict[str, object] | None = None) -> HermeticLauncher:
    return HermeticLauncher(
        container_image_ref="registry.example/supernova@sha256:" + "1" * 64,
        command=("/opt/supernova/executor", "--stdio"),
        inference_runtime_sha256="2" * 64,
        model_weights_sha256="3" * 64,
        tokenizer_sha256="4" * 64,
        exact_model_version="fixture-model@sha256:" + "5" * 64,
        model_provider="HERMETIC_LOCAL_MODEL",
        generation_settings=settings or {
            "max_output_tokens": 4096,
            "sampling": "GREEDY",
            "temperature": 0,
        },
        memory_bytes=8_589_934_592,
        nano_cpus=2_000_000_000,
        timeout_seconds=600,
        max_output_bytes=1_048_576,
    )


class _FakeDocker:
    def __init__(
        self,
        launcher: HermeticLauncher,
        *,
        network_mode: str = "none",
        environment: list[str] | None = None,
        teardown_failure: bool = False,
    ) -> None:
        self.launcher = launcher
        self.network_mode = network_mode
        self.environment = environment or []
        self.teardown_failure = teardown_failure
        self.calls: list[tuple[list[str], bytes | None, int | None]] = []
        self.started = False
        self.removed = False

    def inspection(self) -> dict[str, object]:
        return {
            "Image": IMAGE_ID,
            "HostConfig": {
                "NetworkMode": self.network_mode,
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
                "PidsLimit": 256,
                "Memory": self.launcher.memory_bytes,
                "NanoCpus": self.launcher.nano_cpus,
                "Tmpfs": dict(TMPFS),
                "Binds": None,
            },
            "Config": {
                "OpenStdin": True,
                "Tty": False,
                "Cmd": list(self.launcher.command),
                "Entrypoint": ["/usr/bin/tini", "--"],
                "Env": list(self.environment),
                "Labels": {"org.opencontainers.image.revision": "fixture"},
                "User": "65532",
                "WorkingDir": "/work",
            },
            "Mounts": [],
            "State": {
                "Status": "exited" if self.started else "created",
                "ExitCode": 0,
            },
        }

    def __call__(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        input_bytes: bytes | None = None,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        argv = list(argv)
        self.calls.append((argv, input_bytes, timeout))
        if argv[:3] == ["docker", "version", "--format"]:
            return _completed(
                argv,
                stdout=json.dumps(
                    {
                        "Client": {"Version": "29.0.0", "ApiVersion": "1.52"},
                        "Server": {"Version": "29.0.0", "ApiVersion": "1.52"},
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8"),
            )
        if argv[:3] == ["docker", "image", "inspect"]:
            return _completed(
                argv,
                stdout=json.dumps(
                    [{"Id": IMAGE_ID, "RepoDigests": [self.launcher.container_image_ref]}]
                ).encode("utf-8"),
            )
        if argv[:2] == ["docker", "create"]:
            return _completed(argv, stdout=(CONTAINER_ID + "\n").encode("ascii"))
        if argv[:2] == ["docker", "inspect"]:
            if self.removed and not self.teardown_failure:
                return _completed(argv, returncode=1, stderr=b"No such container")
            return _completed(
                argv, stdout=json.dumps([self.inspection()]).encode("utf-8")
            )
        if argv[:2] == ["docker", "start"]:
            self.started = True
            request = json.loads((input_bytes or b"{}").decode("utf-8"))
            if request.get("operation") == "PREFLIGHT":
                response = {
                    "executor_artifact_sha256": self.launcher.executor_artifact_sha256,
                    "model_identity_sha256": self.launcher.model_identity_sha256,
                    "schema": "supernova.hermetic-preflight-response.v1",
                    "status": "READY",
                }
                return _completed(
                    argv,
                    stdout=json.dumps(
                        response, separators=(",", ":"), sort_keys=True
                    ).encode("utf-8"),
                )
            return _completed(argv, stdout=b"opaque-model-response")
        if argv[:3] == ["docker", "rm", "--force"]:
            self.removed = True
            return _completed(argv, stdout=CONTAINER_ID.encode("ascii"))
        raise AssertionError(f"unexpected command: {argv}")


class ConfirmatorySupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.launcher = _launcher()
        self.receipt_private = _raw_private(Ed25519PrivateKey.generate())
        self.root_private = _raw_private(Ed25519PrivateKey.generate())

    def _preflight(self, fake: _FakeDocker):
        with patch(
            "supernova_goal1.confirmatory_supervisor._invoke",
            side_effect=fake,
        ):
            return run_hermetic_preflight(
                self.launcher,
                issuer_id="production-hermetic-supervisor-v1",
                validator_id="host-preflight-validator-v1",
                receipt_private_key=self.receipt_private,
            )

    def _sealed_and_validated(self):
        with patch(
            "supernova_goal1.confirmatory_supervisor._invoke",
            side_effect=_FakeDocker(self.launcher),
        ):
            sealed = provision_execution_authority(
                PROTOCOL,
                GOAL1,
                self.launcher,
                authority_id="goal1-confirmatory-authority-v1",
                root_key_id="goal1-confirmatory-root-v1",
                root_private_key=self.root_private,
                receipt_issuer_id="production-hermetic-supervisor-v1",
                receipt_private_key=self.receipt_private,
                validator_id="host-preflight-validator-v1",
                pool_id="local-hermetic-pool-v1",
                capacity_binding_sha256="6" * 64,
            )
        root_public = Ed25519PrivateKey.from_private_bytes(
            self.root_private
        ).public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        validation = _validate_authority_artifact(
            sealed.authority,
            protocol=PROTOCOL,
            goal1=GOAL1,
            root_key_id="goal1-confirmatory-root-v1",
            root_public_key=root_public,
        )
        return sealed, _issue_validated_authority(validation)

    def test_preflight_uses_a_fresh_networkless_read_only_container(self) -> None:
        fake = _FakeDocker(self.launcher)
        evidence = self._preflight(fake)
        create = next(argv for argv, _, _ in fake.calls if argv[:2] == ["docker", "create"])
        self.assertIn("--network", create)
        self.assertEqual(create[create.index("--network") + 1], "none")
        self.assertIn("--read-only", create)
        self.assertIn("ALL", create)
        self.assertIn("no-new-privileges:true", create)
        self.assertNotIn("--env", create)
        self.assertNotIn("--mount", create)
        self.assertNotIn("--volume", create)
        self.assertTrue(fake.removed)
        self.assertEqual(evidence.receipt["fresh_process_observed"], True)
        self.assertEqual(evidence.receipt["teardown_observed"], True)
        self.assertEqual(evidence.validation["verdict"], "PASS")

    def test_sealing_emits_only_public_artifacts_and_validates_them(self) -> None:
        sealed, capability = self._sealed_and_validated()
        encoded = json.dumps(
            {"trust_root": sealed.trust_root, "authority": sealed.authority},
            allow_nan=False,
            sort_keys=True,
        )
        self.assertNotIn("private", encoded.lower())
        self.assertEqual(
            capability.executor_artifact_sha256,
            self.launcher.executor_artifact_sha256,
        )
        self.assertEqual(capability.model_identity_sha256, self.launcher.model_identity_sha256)

    def test_supervised_attempt_binds_bytes_and_verified_teardown(self) -> None:
        _, capability = self._sealed_and_validated()
        fake = _FakeDocker(self.launcher)
        request = b'{"prompt":"prove theorem"}'
        with patch(
            "supernova_goal1.confirmatory_supervisor._invoke",
            side_effect=fake,
        ):
            result = run_supervised_attempt(
                self.launcher,
                capability,
                request,
                receipt_private_key=self.receipt_private,
                confirmatory_manifest_sha256="7" * 64,
                run_id="goal1-run-v1",
                protocol_dispatch_id="dispatch-" + "8" * 64,
                dispatch_id="9" * 64,
                problem_id="mathd_algebra_1",
                arm="ordinary",
                attempt_index=0,
                sequence=0,
            )
        self.assertEqual(result.response, b"opaque-model-response")
        self.assertEqual(
            result.context_receipt.request_artifact_sha256,
            __import__("hashlib").sha256(request).hexdigest(),
        )
        self.assertEqual(result.context_receipt.initial_context_sha256, EMPTY_CONTEXT_SHA256)
        self.assertTrue(result.context_receipt.teardown_observed)
        self.assertTrue(fake.removed)

    def test_network_or_clean_image_drift_blocks_before_model_start(self) -> None:
        bad = _FakeDocker(self.launcher, network_mode="bridge")
        with self.assertRaisesRegex(SupervisorError, "security configuration drifted"):
            self._preflight(bad)
        self.assertFalse(bad.started)
        self.assertTrue(bad.removed)

        _, capability = self._sealed_and_validated()
        drifted = _FakeDocker(self.launcher, environment=["UNEXPECTED=1"])
        with patch(
            "supernova_goal1.confirmatory_supervisor._invoke",
            side_effect=drifted,
        ):
            with self.assertRaisesRegex(SupervisorError, "digest drifted"):
                run_supervised_attempt(
                    self.launcher,
                    capability,
                    b"request",
                    receipt_private_key=self.receipt_private,
                    confirmatory_manifest_sha256="7" * 64,
                    run_id="goal1-run-v1",
                    protocol_dispatch_id="dispatch-" + "8" * 64,
                    dispatch_id="9" * 64,
                    problem_id="mathd_algebra_1",
                    arm="ordinary",
                    attempt_index=0,
                    sequence=0,
                )
        self.assertFalse(drifted.started)
        self.assertTrue(drifted.removed)

    def test_teardown_failure_cannot_emit_a_receipt(self) -> None:
        fake = _FakeDocker(self.launcher, teardown_failure=True)
        with self.assertRaisesRegex(SupervisorError, "teardown was not observed"):
            self._preflight(fake)

    def test_wrong_receipt_key_blocks_before_execution(self) -> None:
        _, capability = self._sealed_and_validated()
        fake = _FakeDocker(self.launcher)
        with patch(
            "supernova_goal1.confirmatory_supervisor._invoke",
            side_effect=fake,
        ):
            with self.assertRaisesRegex(PermissionError, "does not match"):
                run_supervised_attempt(
                    self.launcher,
                    capability,
                    b"request",
                    receipt_private_key=_raw_private(Ed25519PrivateKey.generate()),
                    confirmatory_manifest_sha256="7" * 64,
                    run_id="goal1-run-v1",
                    protocol_dispatch_id="dispatch-" + "8" * 64,
                    dispatch_id="9" * 64,
                    problem_id="mathd_algebra_1",
                    arm="ordinary",
                    attempt_index=0,
                    sequence=0,
                )
        self.assertEqual(fake.calls, [])

    def test_launcher_copies_mutable_generation_settings(self) -> None:
        source = {"temperature": 0}
        launcher = _launcher(source)
        before = launcher.generation_settings_sha256
        source["temperature"] = 1
        self.assertEqual(launcher.generation_settings_sha256, before)


if __name__ == "__main__":
    unittest.main()
