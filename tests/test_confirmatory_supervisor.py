from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from supernova_goal1.confirmatory_supervisor import (
    EMPTY_CONTEXT_SHA256,
    ExecutorProcessError,
    ExecutorResponseError,
    GENERATION_RESPONSE_SCHEMA,
    HermeticLauncher,
    SupervisorError,
    load_launcher_file,
    provision_execution_authority,
    provision_repository_execution_authority,
    run_hermetic_preflight,
    run_supervised_attempt,
)
from supernova_goal1.execution_authority import (
    _issue_validated_authority,
    _validate_authority_artifact,
    canonical_sha256,
    load_execution_authority,
)


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = json.loads(
    (ROOT / "goal1" / "CONFIRMATORY_PROTOCOL.json").read_text(encoding="utf-8")
)
GOAL1 = json.loads((ROOT / "goal1" / "GOAL1.json").read_text(encoding="utf-8"))
RUNTIME = json.loads(
    (ROOT / "goal1" / "CONFIRMATORY_RUNTIME.json").read_text(encoding="utf-8")
)
BUILD_LOCK = json.loads(
    (ROOT / "runtime" / "goal1_hermetic_executor" / "BUILD_LOCK.json").read_text(
        encoding="utf-8"
    )
)
PUBLISHED_IMAGE = json.loads(
    (
        ROOT
        / "runtime"
        / "goal1_hermetic_executor"
        / "PUBLISHED_IMAGE.json"
    ).read_text(encoding="utf-8")
)
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
        container_image_ref=(
            "ghcr.io/kitahl/supernova-goal1-executor@sha256:" + "1" * 64
        ),
        command=("/opt/supernova/executor", "--stdio"),
        inference_runtime_sha256="2" * 64,
        model_weights_sha256="3" * 64,
        tokenizer_sha256="3" * 64,
        exact_model_version="fixture-model@sha256:" + "5" * 64,
        model_provider="HERMETIC_LOCAL_MODEL",
        generation_settings=settings or {
            "max_output_tokens": 4096,
            "sampling": "GREEDY",
            "temperature": 0,
        },
        image_environment=(),
        container_user="65532:65532",
        memory_bytes=8_589_934_592,
        nano_cpus=2_000_000_000,
        timeout_seconds=600,
        max_output_bytes=1_048_576,
    )



def _launcher_config(launcher: HermeticLauncher) -> dict[str, object]:
    return {
        "command": list(launcher.command),
        "container_image_ref": launcher.container_image_ref,
        "container_user": launcher.container_user,
        "exact_model_version": launcher.exact_model_version,
        "generation_settings": launcher.generation_settings,
        "image_environment": list(launcher.image_environment),
        "inference_runtime_sha256": launcher.inference_runtime_sha256,
        "max_output_bytes": launcher.max_output_bytes,
        "memory_bytes": launcher.memory_bytes,
        "model_provider": launcher.model_provider,
        "model_weights_sha256": launcher.model_weights_sha256,
        "nano_cpus": launcher.nano_cpus,
        "schema": "supernova.hermetic-launcher.v1",
        "timeout_seconds": launcher.timeout_seconds,
        "tokenizer_sha256": launcher.tokenizer_sha256,
    }


def _capacity_config(launcher: HermeticLauncher) -> dict[str, object]:
    return {
        "schema": "supernova.confirmatory-capacity-binding.v1",
        "pool_id": "fixture-hermetic-pool-v1",
        "platform": "linux/amd64",
        "pool_instance_count": 1,
        "executor_image_ref": launcher.container_image_ref,
        "launcher_artifact_sha256": launcher.launcher_artifact_sha256,
        "model_slot": {
            "memory_bytes": launcher.memory_bytes,
            "nano_cpus": launcher.nano_cpus,
            "timeout_seconds": launcher.timeout_seconds,
            "max_output_bytes": launcher.max_output_bytes,
            "pids_limit": 256,
            "runtime": "runc",
            "network": "none",
            "gpu_device_requests": 0,
        },
        "verifier_slot": dict(RUNTIME["resource_limits"]),
        "concurrency": {
            "max_model_dispatches": 1,
            "max_verifier_processes": 1,
            "protocol_rule": PROTOCOL["sealed_rules"]["deterministic_schedule"][
                "concurrency"
            ],
        },
        "selection_after_manifest": "BLOCKED",
    }


def _prepare_repository_bindings(repository: Path, launcher: HermeticLauncher) -> None:
    goal = repository / "goal1"
    build_context = repository / "runtime" / "goal1_hermetic_executor"
    goal.mkdir(parents=True)
    build_context.mkdir(parents=True)
    for name, value in (
        ("CONFIRMATORY_PROTOCOL.json", PROTOCOL),
        ("GOAL1.json", GOAL1),
        ("CONFIRMATORY_RUNTIME.json", RUNTIME),
        ("CONFIRMATORY_EXECUTOR_LAUNCHER.json", _launcher_config(launcher)),
        ("CONFIRMATORY_CAPACITY_BINDING.json", _capacity_config(launcher)),
    ):
        (goal / name).write_text(json.dumps(value), encoding="utf-8")
    build_lock = json.loads(json.dumps(BUILD_LOCK))
    build_lock["command"] = list(launcher.command)
    build_lock["container_user"] = launcher.container_user
    build_lock["generation_settings"] = launcher.generation_settings
    build_lock["image_environment"] = list(launcher.image_environment)
    build_lock["model"]["exact_version"] = launcher.exact_model_version
    build_lock["model"]["sha256"] = launcher.model_weights_sha256
    build_lock_path = build_context / "BUILD_LOCK.json"
    build_lock_path.write_text(json.dumps(build_lock), encoding="utf-8")
    publication = json.loads(json.dumps(PUBLISHED_IMAGE))
    publication["image_ref"] = launcher.container_image_ref
    publication["image_digest"] = launcher.container_image_digest
    publication["llama_cli_sha256"] = launcher.inference_runtime_sha256
    publication["model_sha256"] = launcher.model_weights_sha256
    publication["build_lock_sha256"] = __import__("hashlib").sha256(
        build_lock_path.read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()
    (build_context / "PUBLISHED_IMAGE.json").write_text(
        json.dumps(publication), encoding="utf-8"
    )


class _FakeDocker:
    def __init__(
        self,
        launcher: HermeticLauncher,
        *,
        network_mode: str = "none",
        environment: list[str] | None = None,
        teardown_failure: bool = False,
        remove_failure: bool = False,
        start_failure: bool = False,
        host_overrides: dict[str, object] | None = None,
        generation_stdout: bytes | None = None,
        timeout_on_start: bool = False,
    ) -> None:
        self.launcher = launcher
        self.network_mode = network_mode
        self.environment = environment or []
        self.teardown_failure = teardown_failure
        self.remove_failure = remove_failure
        self.start_failure = start_failure
        self.host_overrides = host_overrides or {}
        self.generation_stdout = generation_stdout
        self.timeout_on_start = timeout_on_start
        self.calls: list[tuple[list[str], bytes | None, int | None]] = []
        self.started = False
        self.removed = False

    def inspection(self) -> dict[str, object]:
        host = {
            "NetworkMode": self.network_mode,
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "Devices": None,
            "DeviceRequests": None,
            "SecurityOpt": ["no-new-privileges:true"],
            "PidsLimit": 256,
            "Memory": self.launcher.memory_bytes,
            "NanoCpus": self.launcher.nano_cpus,
            "IpcMode": "none",
            "PidMode": "",
            "UTSMode": "",
            "UsernsMode": "",
            "CgroupnsMode": "private",
            "PublishAllPorts": False,
            "PortBindings": {},
            "Links": None,
            "ExtraHosts": None,
            "GroupAdd": None,
            "VolumesFrom": None,
            "AutoRemove": False,
            "OomKillDisable": False,
            "Init": True,
            "Runtime": "runc",
            "Isolation": "",
            "RestartPolicy": {"MaximumRetryCount": 0, "Name": "no"},
            "Tmpfs": dict(TMPFS),
            "Binds": None,
        }
        host.update(self.host_overrides)
        return {
            "Image": IMAGE_ID,
            "HostConfig": host,
            "Config": {
                "OpenStdin": True,
                "Tty": False,
                "Cmd": list(self.launcher.command),
                "Entrypoint": ["/usr/bin/tini", "--"],
                "Env": list(self.environment),
                "Labels": {"org.opencontainers.image.revision": "fixture"},
                "User": self.launcher.container_user,
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
            if self.timeout_on_start:
                from supernova_goal1.confirmatory_supervisor import _HostCommandTimeout

                raise _HostCommandTimeout(
                    argv, timeout, b"partial", b"still running"
                )
            if self.start_failure:
                return _completed(argv, returncode=1, stderr=b"executor failed")
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
            if self.generation_stdout is not None:
                return _completed(argv, stdout=self.generation_stdout)
            return _completed(
                argv,
                stdout=json.dumps(
                    {
                        "completion_utf8": "opaque-model-response",
                        "schema": GENERATION_RESPONSE_SCHEMA,
                        "status": "ANSWERED",
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8"),
            )
        if argv[:3] == ["docker", "rm", "--force"]:
            if self.remove_failure:
                return _completed(argv, returncode=1, stderr=b"remove failed")
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

    def _run_attempt(self, capability, request: bytes):
        return run_supervised_attempt(
            self.launcher,
            capability,
            request,
            receipt_private_key=self.receipt_private,
            confirmatory_manifest_sha256="7" * 64,
            run_id="goal1-run-v1",
            protocol_dispatch_id="dispatch-" + "8" * 64,
            dispatch_id="9" * 64,
            problem_id="aime_1983_p1",
            arm="ordinary",
            attempt_index=0,
            sequence=0,
        )

    def test_preflight_uses_a_fresh_networkless_read_only_container(self) -> None:
        fake = _FakeDocker(self.launcher)
        evidence = self._preflight(fake)
        create = next(argv for argv, _, _ in fake.calls if argv[:2] == ["docker", "create"])
        self.assertIn("--network", create)
        self.assertEqual(create[create.index("--network") + 1], "none")
        self.assertIn("--read-only", create)
        self.assertNotIn("--pid", create)
        self.assertIn("ALL", create)
        self.assertIn("no-new-privileges:true", create)
        self.assertNotIn("--env", create)
        self.assertNotIn("--uts", create)
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
        self.assertEqual(result.process_observation.exit_status, 0)
        self.assertFalse(result.process_observation.timed_out)
        self.assertEqual(
            result.context_receipt.request_artifact_sha256,
            __import__("hashlib").sha256(request).hexdigest(),
        )
        self.assertEqual(result.context_receipt.initial_context_sha256, EMPTY_CONTEXT_SHA256)
        self.assertTrue(result.context_receipt.teardown_observed)
        self.assertTrue(fake.removed)

    def test_captured_llama_cli_transcript_is_rejected_before_verification(self) -> None:
        _, capability = self._sealed_and_validated()
        transcript = (
            b"Loading model...\nllama-cli\nmodel : /opt/supernova/model.gguf\n"
            b"available commands:\nFROZEN_THEOREM_NAME=aime_1983_p1\n"
            b"The answer is \\boxed{3}.\nExiting...\n"
        )
        fake = _FakeDocker(self.launcher, generation_stdout=transcript)
        with (
            patch(
                "supernova_goal1.confirmatory_supervisor._invoke",
                side_effect=fake,
            ),
            self.assertRaisesRegex(
                ExecutorResponseError, "not one generation response"
            ) as caught,
        ):
            self._run_attempt(capability, b'{"prompt":"frozen prompt"}')
        self.assertEqual(caught.exception.code, "MALFORMED_FRAME")
        self.assertEqual(caught.exception.observation.exit_status, 0)
        self.assertTrue(fake.removed)

    def test_missing_multiple_and_malformed_generation_frames_are_typed(self) -> None:
        _, capability = self._sealed_and_validated()
        cases = {
            "missing": (b"", "MISSING_FRAME"),
            "multiple": (b"{}\n{}", "MULTIPLE_FRAMES"),
            "malformed": (b'{"schema":', "MALFORMED_FRAME"),
        }
        for name, (stdout, code) in cases.items():
            with self.subTest(name=name):
                fake = _FakeDocker(self.launcher, generation_stdout=stdout)
                with (
                    patch(
                        "supernova_goal1.confirmatory_supervisor._invoke",
                        side_effect=fake,
                    ),
                    self.assertRaises(ExecutorResponseError) as caught,
                ):
                    self._run_attempt(capability, b'{"prompt":"frozen prompt"}')
                self.assertEqual(caught.exception.code, code)
                self.assertTrue(fake.removed)

    def test_executor_timeout_has_no_exit_status_and_preserves_observation(self) -> None:
        _, capability = self._sealed_and_validated()
        fake = _FakeDocker(self.launcher, timeout_on_start=True)
        with (
            patch(
                "supernova_goal1.confirmatory_supervisor._invoke",
                side_effect=fake,
            ),
            self.assertRaisesRegex(ExecutorProcessError, "timed out") as caught,
        ):
            self._run_attempt(capability, b'{"prompt":"frozen prompt"}')
        observation = caught.exception.observation
        self.assertEqual(observation.phase, "GENERATION")
        self.assertEqual(observation.termination_cause, "TIMEOUT_KILLED")
        self.assertTrue(observation.timed_out)
        self.assertIsNone(observation.exit_status)
        self.assertEqual(observation.stdout_bytes, len(b"partial"))
        self.assertEqual(observation.stderr_bytes, len(b"still running"))
        self.assertTrue(observation.teardown_observed)
        self.assertTrue(fake.removed)

    def test_host_timeout_preserves_partial_streams_without_exit_status(self) -> None:
        from supernova_goal1.confirmatory_supervisor import (
            _HostCommandTimeout,
            _invoke,
        )

        expired = subprocess.TimeoutExpired(
            cmd=["docker", "start"],
            timeout=1,
            output=b"partial stdout",
            stderr=b"partial stderr",
        )
        with (
            patch(
                "supernova_goal1.confirmatory_supervisor.subprocess.run",
                side_effect=expired,
            ),
            self.assertRaises(_HostCommandTimeout) as caught,
        ):
            _invoke(["docker", "start"], timeout=1)
        self.assertEqual(caught.exception.stdout, b"partial stdout")
        self.assertEqual(caught.exception.stderr, b"partial stderr")
        self.assertEqual(caught.exception.timeout_seconds, 1)

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
            with self.assertRaisesRegex(
                SupervisorError, "security configuration drifted"
            ):
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
        with self.assertRaisesRegex(SupervisorError, "cleanup was not observed"):
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

    def test_launcher_rejects_credentials_and_root_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "not allowed"):
            HermeticLauncher(
                container_image_ref=self.launcher.container_image_ref,
                command=self.launcher.command,
                inference_runtime_sha256=self.launcher.inference_runtime_sha256,
                model_weights_sha256=self.launcher.model_weights_sha256,
                tokenizer_sha256=self.launcher.tokenizer_sha256,
                exact_model_version=self.launcher.exact_model_version,
                model_provider=self.launcher.model_provider,
                generation_settings=self.launcher.generation_settings,
                image_environment=("AWS_SECRET_ACCESS_KEY=leaked",),
                container_user="65532:65532",
                memory_bytes=self.launcher.memory_bytes,
                nano_cpus=self.launcher.nano_cpus,
                timeout_seconds=self.launcher.timeout_seconds,
                max_output_bytes=self.launcher.max_output_bytes,
            )
        with self.assertRaisesRegex(ValueError, "non-root"):
            HermeticLauncher(
                container_image_ref=self.launcher.container_image_ref,
                command=self.launcher.command,
                inference_runtime_sha256=self.launcher.inference_runtime_sha256,
                model_weights_sha256=self.launcher.model_weights_sha256,
                tokenizer_sha256=self.launcher.tokenizer_sha256,
                exact_model_version=self.launcher.exact_model_version,
                model_provider=self.launcher.model_provider,
                generation_settings=self.launcher.generation_settings,
                image_environment=(),
                container_user="0:0",
                memory_bytes=self.launcher.memory_bytes,
                nano_cpus=self.launcher.nano_cpus,
                timeout_seconds=self.launcher.timeout_seconds,
                max_output_bytes=self.launcher.max_output_bytes,
            )

    def test_privilege_device_and_namespace_drift_are_rejected(self) -> None:
        attacks = {
            "privileged": {"Privileged": True},
            "capability": {"CapAdd": ["SYS_ADMIN"]},
            "device": {"Devices": [{"PathOnHost": "/dev/sda"}]},
            "device_request": {"DeviceRequests": [{"Capabilities": [["gpu"]]}]},
            "missing_pid_observation": {"PidMode": None},
            "host_pid": {"PidMode": "host"},
            "container_pid": {"PidMode": "container:other"},
            "host_ipc": {"IpcMode": "host"},
            "host_uts": {"UTSMode": "host"},
            "host_userns": {"UsernsMode": "host"},
        }
        for name, override in attacks.items():
            with self.subTest(name=name):
                fake = _FakeDocker(self.launcher, host_overrides=override)
                with self.assertRaisesRegex(
                    SupervisorError, "security configuration drifted"
                ):
                    self._preflight(fake)
                self.assertFalse(fake.started)
                self.assertTrue(fake.removed)

    def test_cleanup_failure_overrides_start_failure_with_specific_error(self) -> None:
        fake = _FakeDocker(
            self.launcher, start_failure=True, remove_failure=True
        )
        with self.assertRaisesRegex(
            SupervisorError, "cleanup was not observed"
        ) as captured:
            self._preflight(fake)
        self.assertIsNotNone(captured.exception.__cause__)
        self.assertTrue(fake.started)
        self.assertFalse(fake.removed)

    def test_cleanup_failure_overrides_prestart_drift_with_specific_error(self) -> None:
        fake = _FakeDocker(
            self.launcher,
            network_mode="bridge",
            remove_failure=True,
        )
        with self.assertRaisesRegex(
            SupervisorError, "cleanup was not observed"
        ) as captured:
            self._preflight(fake)
        self.assertIsNotNone(captured.exception.__cause__)
        self.assertFalse(fake.started)
        self.assertFalse(fake.removed)

    def test_launcher_copies_mutable_generation_settings(self) -> None:
        source = {"temperature": 0}
        launcher = _launcher(source)
        before = launcher.generation_settings_sha256
        source["temperature"] = 1
        self.assertEqual(launcher.generation_settings_sha256, before)


    def test_repository_provisioner_has_no_launcher_or_capacity_injection(self) -> None:
        parameters = inspect.signature(
            provision_repository_execution_authority
        ).parameters
        self.assertEqual(
            list(parameters),
            [
                "root_key_path",
                "receipt_key_path",
                "authority_id",
                "root_key_id",
                "receipt_issuer_id",
                "validator_id",
            ],
        )

    def test_operator_provisioner_writes_only_fixed_public_artifacts(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            base = Path(directory)
            repository = base / "repository"
            goal = repository / "goal1"
            _prepare_repository_bindings(repository, self.launcher)
            root_key_path = base / "root.key"
            receipt_key_path = base / "receipt.key"
            root_key_path.write_bytes(self.root_private)
            receipt_key_path.write_bytes(self.receipt_private)

            fake = _FakeDocker(self.launcher)
            with (
                patch(
                    "supernova_goal1.confirmatory_supervisor._repository_root",
                    return_value=repository,
                ),
                patch(
                    "supernova_goal1.execution_authority._repository_root",
                    return_value=repository,
                ),
                patch(
                    "supernova_goal1.confirmatory_supervisor._invoke",
                    side_effect=fake,
                ),
                patch(
                    "supernova_goal1.confirmatory_supervisor.os.link",
                    wraps=os.link,
                ) as link_calls,
            ):
                sealed = provision_repository_execution_authority(
                    root_key_path,
                    receipt_key_path,
                    authority_id="goal1-confirmatory-authority-v1",
                    root_key_id="goal1-confirmatory-root-v1",
                    receipt_issuer_id="production-hermetic-supervisor-v1",
                    validator_id="host-preflight-validator-v1",
                )

            trust_path = goal / "CONFIRMATORY_TRUST_ROOT.json"
            authority_path = goal / "CONFIRMATORY_EXECUTION_AUTHORITY.json"
            self.assertEqual(json.loads(trust_path.read_text()), sealed.trust_root)
            self.assertEqual(json.loads(authority_path.read_text()), sealed.authority)
            self.assertEqual(
                sealed.authority["serving_pool_policy"]["pool_id"],
                "fixture-hermetic-pool-v1",
            )
            self.assertEqual(
                sealed.authority["serving_pool_policy"]["capacity_binding_sha256"],
                canonical_sha256(_capacity_config(self.launcher)),
            )
            public_bytes = trust_path.read_bytes() + authority_path.read_bytes()
            self.assertNotIn(self.root_private, public_bytes)
            self.assertNotIn(self.receipt_private, public_bytes)
            self.assertTrue(fake.removed)
            self.assertEqual(
                [Path(call.args[1]).name for call in link_calls.call_args_list],
                [
                    "CONFIRMATORY_EXECUTION_AUTHORITY.json",
                    "CONFIRMATORY_TRUST_ROOT.json",
                ],
            )
            with patch(
                "supernova_goal1.execution_authority._repository_root",
                return_value=repository,
            ):
                capability = load_execution_authority(PROTOCOL, GOAL1)
            self.assertEqual(
                capability.executor_artifact_sha256,
                self.launcher.executor_artifact_sha256,
            )

            launcher_binding_path = (
                goal / "CONFIRMATORY_EXECUTOR_LAUNCHER.json"
            )
            capacity_binding_path = (
                goal / "CONFIRMATORY_CAPACITY_BINDING.json"
            )
            for fixed_path in (launcher_binding_path, capacity_binding_path):
                original_bytes = fixed_path.read_bytes()
                fixed_path.unlink()
                with (
                    patch(
                        "supernova_goal1.execution_authority._repository_root",
                        return_value=repository,
                    ),
                    self.subTest(missing=fixed_path.name),
                    self.assertRaisesRegex(PermissionError, "launcher/capacity"),
                ):
                    load_execution_authority(PROTOCOL, GOAL1)
                fixed_path.write_bytes(original_bytes)

            capacity = json.loads(capacity_binding_path.read_text(encoding="utf-8"))
            capacity_binding_path.write_text(
                json.dumps(capacity, indent=4, sort_keys=False), encoding="utf-8"
            )
            with patch(
                "supernova_goal1.execution_authority._repository_root",
                return_value=repository,
            ):
                self.assertEqual(
                    load_execution_authority(PROTOCOL, GOAL1).authority_sha256,
                    capability.authority_sha256,
                )

            launcher_config = json.loads(
                launcher_binding_path.read_text(encoding="utf-8")
            )
            launcher_config["inference_runtime_sha256"] = "9" * 64
            launcher_binding_path.write_text(
                json.dumps(launcher_config), encoding="utf-8"
            )
            with (
                patch(
                    "supernova_goal1.execution_authority._repository_root",
                    return_value=repository,
                ),
                self.assertRaisesRegex(PermissionError, "launcher/capacity"),
            ):
                load_execution_authority(PROTOCOL, GOAL1)
            launcher_binding_path.write_text(
                json.dumps(_launcher_config(self.launcher)), encoding="utf-8"
            )

            second = _FakeDocker(self.launcher)
            with (
                patch(
                    "supernova_goal1.confirmatory_supervisor._repository_root",
                    return_value=repository,
                ),
                patch(
                    "supernova_goal1.confirmatory_supervisor._invoke",
                    side_effect=second,
                ),
                self.assertRaisesRegex(FileExistsError, "refusing to overwrite"),
            ):
                provision_repository_execution_authority(
                    root_key_path,
                    receipt_key_path,
                    authority_id="goal1-confirmatory-authority-v1",
                    root_key_id="goal1-confirmatory-root-v1",
                    receipt_issuer_id="production-hermetic-supervisor-v1",
                    validator_id="host-preflight-validator-v1",
                )
            self.assertEqual(second.calls, [])

    def test_operator_provisioner_rejects_repository_key_and_launcher_drift(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            base = Path(directory)
            repository = base / "repository"
            goal = repository / "goal1"
            goal.mkdir(parents=True)
            launcher_path = base / "launcher.json"
            config = _launcher_config(self.launcher)
            config["unexpected"] = "drift"
            launcher_path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fields differ"):
                load_launcher_file(launcher_path)
            launcher_path.write_text(
                '{"schema":"supernova.hermetic-launcher.v1",'
                '"schema":"supernova.hermetic-launcher.v1"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate JSON object key"):
                load_launcher_file(launcher_path)

            key_in_repository = goal / "root.key"
            key_in_repository.write_bytes(self.root_private)
            receipt_key_path = base / "receipt.key"
            receipt_key_path.write_bytes(self.receipt_private)
            with (
                patch(
                    "supernova_goal1.confirmatory_supervisor._repository_root",
                    return_value=repository,
                ),
                self.assertRaisesRegex(PermissionError, "outside the repository"),
            ):
                provision_repository_execution_authority(
                    key_in_repository,
                    receipt_key_path,
                    authority_id="goal1-confirmatory-authority-v1",
                    root_key_id="goal1-confirmatory-root-v1",
                    receipt_issuer_id="production-hermetic-supervisor-v1",
                    validator_id="host-preflight-validator-v1",
                )

    def test_capacity_drift_blocks_before_docker_or_key_read(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            base = Path(directory)
            repository = base / "repository"
            _prepare_repository_bindings(repository, self.launcher)
            capacity_path = (
                repository / "goal1" / "CONFIRMATORY_CAPACITY_BINDING.json"
            )
            capacity = json.loads(capacity_path.read_text(encoding="utf-8"))
            capacity["model_slot"]["network"] = "bridge"
            capacity_path.write_text(json.dumps(capacity), encoding="utf-8")
            root_key_path = base / "root.key"
            receipt_key_path = base / "receipt.key"
            root_key_path.write_bytes(self.root_private)
            receipt_key_path.write_bytes(self.receipt_private)
            fake = _FakeDocker(self.launcher)
            with (
                patch(
                    "supernova_goal1.confirmatory_supervisor._repository_root",
                    return_value=repository,
                ),
                patch(
                    "supernova_goal1.confirmatory_supervisor._invoke",
                    side_effect=fake,
                ),
                patch(
                    "supernova_goal1.confirmatory_supervisor.load_private_key_file"
                ) as key_loader,
                self.assertRaisesRegex(ValueError, "model_slot"),
            ):
                provision_repository_execution_authority(
                    root_key_path,
                    receipt_key_path,
                    authority_id="goal1-confirmatory-authority-v1",
                    root_key_id="goal1-confirmatory-root-v1",
                    receipt_issuer_id="production-hermetic-supervisor-v1",
                    validator_id="host-preflight-validator-v1",
                )
            self.assertEqual(fake.calls, [])
            key_loader.assert_not_called()

    def test_publication_evidence_blocks_synchronized_identity_tamper(self) -> None:
        from tempfile import TemporaryDirectory

        for mutation in ("image", "runtime", "model"):
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                base = Path(directory)
                repository = base / "repository"
                _prepare_repository_bindings(repository, self.launcher)
                goal = repository / "goal1"
                launcher_path = goal / "CONFIRMATORY_EXECUTOR_LAUNCHER.json"
                capacity_path = goal / "CONFIRMATORY_CAPACITY_BINDING.json"
                lock_path = (
                    repository / "runtime" / "goal1_hermetic_executor" / "BUILD_LOCK.json"
                )
                launcher_config = json.loads(
                    launcher_path.read_text(encoding="utf-8")
                )
                capacity = json.loads(capacity_path.read_text(encoding="utf-8"))
                if mutation == "image":
                    launcher_config["container_image_ref"] = (
                        "registry.example/altered@sha256:" + "9" * 64
                    )
                    launcher_path.write_text(
                        json.dumps(launcher_config), encoding="utf-8"
                    )
                    altered = load_launcher_file(launcher_path)
                    capacity["executor_image_ref"] = altered.container_image_ref
                    capacity["launcher_artifact_sha256"] = (
                        altered.launcher_artifact_sha256
                    )
                elif mutation == "runtime":
                    launcher_config["inference_runtime_sha256"] = "9" * 64
                    launcher_path.write_text(
                        json.dumps(launcher_config), encoding="utf-8"
                    )
                else:
                    launcher_config["model_weights_sha256"] = "9" * 64
                    launcher_config["tokenizer_sha256"] = "9" * 64
                    launcher_path.write_text(
                        json.dumps(launcher_config), encoding="utf-8"
                    )
                    lock = json.loads(lock_path.read_text(encoding="utf-8"))
                    lock["model"]["sha256"] = "9" * 64
                    lock_path.write_text(json.dumps(lock), encoding="utf-8")
                capacity_path.write_text(json.dumps(capacity), encoding="utf-8")

                root_key_path = base / "root.key"
                receipt_key_path = base / "receipt.key"
                root_key_path.write_bytes(self.root_private)
                receipt_key_path.write_bytes(self.receipt_private)
                fake = _FakeDocker(self.launcher)
                with (
                    patch(
                        "supernova_goal1.confirmatory_supervisor._repository_root",
                        return_value=repository,
                    ),
                    patch(
                        "supernova_goal1.confirmatory_supervisor._invoke",
                        side_effect=fake,
                    ),
                    patch(
                        "supernova_goal1.confirmatory_supervisor.load_private_key_file"
                    ) as key_loader,
                    self.assertRaisesRegex(ValueError, "publication|published"),
                ):
                    provision_repository_execution_authority(
                        root_key_path,
                        receipt_key_path,
                        authority_id="goal1-confirmatory-authority-v1",
                        root_key_id="goal1-confirmatory-root-v1",
                        receipt_issuer_id="production-hermetic-supervisor-v1",
                        validator_id="host-preflight-validator-v1",
                    )
                self.assertEqual(fake.calls, [])
                key_loader.assert_not_called()

    def test_preflight_failure_never_reads_root_signing_key(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            base = Path(directory)
            repository = base / "repository"
            goal = repository / "goal1"
            _prepare_repository_bindings(repository, self.launcher)
            root_key_path = base / "root.key"
            receipt_key_path = base / "receipt.key"
            root_key_path.write_bytes(self.root_private)
            receipt_key_path.write_bytes(self.receipt_private)
            reads: list[Path] = []

            def key_loader(path: Path) -> bytes:
                reads.append(path)
                if path == receipt_key_path:
                    return self.receipt_private
                raise AssertionError("root key was read before successful preflight")

            fake = _FakeDocker(self.launcher, start_failure=True)
            with (
                patch(
                    "supernova_goal1.confirmatory_supervisor._repository_root",
                    return_value=repository,
                ),
                patch(
                    "supernova_goal1.confirmatory_supervisor.load_private_key_file",
                    side_effect=key_loader,
                ),
                patch(
                    "supernova_goal1.confirmatory_supervisor._invoke",
                    side_effect=fake,
                ),
                self.assertRaises(SupervisorError),
            ):
                provision_repository_execution_authority(
                    root_key_path,
                    receipt_key_path,
                    authority_id="goal1-confirmatory-authority-v1",
                    root_key_id="goal1-confirmatory-root-v1",
                    receipt_issuer_id="production-hermetic-supervisor-v1",
                    validator_id="host-preflight-validator-v1",
                )

            self.assertEqual(reads, [receipt_key_path])
            self.assertFalse((goal / "CONFIRMATORY_TRUST_ROOT.json").exists())
            self.assertFalse((goal / "CONFIRMATORY_EXECUTION_AUTHORITY.json").exists())

    def test_publication_failure_never_exposes_trust_root_commit_marker(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            base = Path(directory)
            repository = base / "repository"
            goal = repository / "goal1"
            _prepare_repository_bindings(repository, self.launcher)
            root_key_path = base / "root.key"
            receipt_key_path = base / "receipt.key"
            root_key_path.write_bytes(self.root_private)
            receipt_key_path.write_bytes(self.receipt_private)
            real_link = os.link
            destinations: list[str] = []

            def fail_before_commit_marker(source: Path, destination: Path) -> None:
                destinations.append(Path(destination).name)
                if Path(destination).name == "CONFIRMATORY_TRUST_ROOT.json":
                    raise OSError("injected publication interruption")
                real_link(source, destination)

            fake = _FakeDocker(self.launcher)
            with (
                patch(
                    "supernova_goal1.confirmatory_supervisor._repository_root",
                    return_value=repository,
                ),
                patch(
                    "supernova_goal1.confirmatory_supervisor._invoke",
                    side_effect=fake,
                ),
                patch(
                    "supernova_goal1.confirmatory_supervisor.os.link",
                    side_effect=fail_before_commit_marker,
                ),
                self.assertRaisesRegex(OSError, "injected publication interruption"),
            ):
                provision_repository_execution_authority(
                    root_key_path,
                    receipt_key_path,
                    authority_id="goal1-confirmatory-authority-v1",
                    root_key_id="goal1-confirmatory-root-v1",
                    receipt_issuer_id="production-hermetic-supervisor-v1",
                    validator_id="host-preflight-validator-v1",
                )

            self.assertEqual(
                destinations,
                [
                    "CONFIRMATORY_EXECUTION_AUTHORITY.json",
                    "CONFIRMATORY_TRUST_ROOT.json",
                ],
            )
            self.assertFalse((goal / "CONFIRMATORY_TRUST_ROOT.json").exists())
            self.assertFalse((goal / "CONFIRMATORY_EXECUTION_AUTHORITY.json").exists())

    def test_same_key_file_is_rejected_before_docker(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            base = Path(directory)
            repository = base / "repository"
            (repository / "goal1").mkdir(parents=True)
            shared_key_path = base / "shared.key"
            shared_key_path.write_bytes(self.receipt_private)
            fake = _FakeDocker(self.launcher)

            with (
                patch(
                    "supernova_goal1.confirmatory_supervisor._repository_root",
                    return_value=repository,
                ),
                patch(
                    "supernova_goal1.confirmatory_supervisor._invoke",
                    side_effect=fake,
                ),
                self.assertRaisesRegex(PermissionError, "key files must be distinct"),
            ):
                provision_repository_execution_authority(
                    shared_key_path,
                    shared_key_path,
                    authority_id="goal1-confirmatory-authority-v1",
                    root_key_id="goal1-confirmatory-root-v1",
                    receipt_issuer_id="production-hermetic-supervisor-v1",
                    validator_id="host-preflight-validator-v1",
                )

            self.assertEqual(fake.calls, [])

    def test_copied_key_identity_is_rejected_after_preflight(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            base = Path(directory)
            repository = base / "repository"
            goal = repository / "goal1"
            _prepare_repository_bindings(repository, self.launcher)
            root_key_path = base / "root.key"
            receipt_key_path = base / "receipt.key"
            root_key_path.write_bytes(self.receipt_private)
            receipt_key_path.write_bytes(self.receipt_private)
            fake = _FakeDocker(self.launcher)

            with (
                patch(
                    "supernova_goal1.confirmatory_supervisor._repository_root",
                    return_value=repository,
                ),
                patch(
                    "supernova_goal1.confirmatory_supervisor._invoke",
                    side_effect=fake,
                ),
                self.assertRaisesRegex(
                    PermissionError, "signing identities must be distinct"
                ),
            ):
                provision_repository_execution_authority(
                    root_key_path,
                    receipt_key_path,
                    authority_id="goal1-confirmatory-authority-v1",
                    root_key_id="goal1-confirmatory-root-v1",
                    receipt_issuer_id="production-hermetic-supervisor-v1",
                    validator_id="host-preflight-validator-v1",
                )

            self.assertTrue(fake.removed)
            self.assertFalse((goal / "CONFIRMATORY_TRUST_ROOT.json").exists())
            self.assertFalse((goal / "CONFIRMATORY_EXECUTION_AUTHORITY.json").exists())

if __name__ == "__main__":
    unittest.main()
