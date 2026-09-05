from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from integration.goal1_validation_pilot import model_container as lifecycle

IMAGE_ID = "sha256:" + "a" * 64
CONTAINER_ID = "b" * 64


class FakeDocker:
    """An explicit fake transport: these tests cannot invoke a real Docker CLI."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], int, bytes | None]] = []
        self.faults: dict[str, Exception | tuple[int, bytes, bytes]] = {}
        self.now_ns = 0
        self.attach_seconds = 1
        self.inspections = 0
        self.security_drift = False

    def state(self, status: str) -> dict:
        return {
            "Image": IMAGE_ID,
            "HostConfig": {
                "NetworkMode": "bridge" if self.security_drift else "none",
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
                "Binds": [],
            },
            "Config": {"User": "65532:65532"},
            "Mounts": [],
            "State": {
                "Status": status,
                "Running": status == "running",
                "OOMKilled": False,
                "ExitCode": 0,
                "Error": "",
            },
        }

    def __call__(self, argv, *, input_bytes=None, timeout):
        self.calls.append((list(argv), timeout, input_bytes))
        if argv[1:3] == ["image", "inspect"]:
            stage, output = "IMAGE_INSPECT", json.dumps([{"Id": IMAGE_ID}]).encode()
        elif argv[1] == "create":
            stage, output = "CONTAINER_CREATE", CONTAINER_ID.encode()
        elif argv[1] == "inspect":
            self.inspections += 1
            stage = "SECURITY_INSPECT" if self.inspections == 1 else "FAILURE_INSPECT"
            # A create fault bypasses the initial security inspection.
            if "CONTAINER_CREATE" in self.faults:
                stage = "FAILURE_INSPECT"
            output = json.dumps([self.state("created" if stage == "SECURITY_INSPECT" else "running")]).encode()
        elif argv[1] == "start":
            stage, output = "MODEL_ATTACH", b"frame"
            if "MODEL_ATTACH" not in self.faults:
                self.now_ns += self.attach_seconds * 1_000_000_000
                if self.attach_seconds > timeout:
                    raise subprocess.TimeoutExpired(argv, timeout, output=b"partial", stderr=b"loading")
        elif argv[1] == "rm":
            stage, output = "TEARDOWN", CONTAINER_ID.encode()
        else:
            raise AssertionError(f"unexpected fake command: {argv}")
        fault = self.faults.get(stage)
        if isinstance(fault, Exception):
            if isinstance(fault, subprocess.TimeoutExpired):
                self.now_ns += timeout * 1_000_000_000
            raise fault
        if fault is not None:
            code, stdout, stderr = fault
            return subprocess.CompletedProcess(argv, code, stdout, stderr)
        return subprocess.CompletedProcess(argv, 0, output, b"executor diagnostic")


class ModelContainerTests(unittest.TestCase):
    def execute(self, docker: FakeDocker, *, parse=None, adapt=None):
        with patch.object(lifecycle.time, "monotonic_ns", side_effect=lambda: docker.now_ns):
            return lifecycle.execute_model_container(
                "frozen-image", b"private prompt",
                parse_generation_frame=(lambda raw: b"raw completion") if parse is None else parse,
                adapt_completion=(lambda raw: (b"adapted completion", "ADAPTED")) if adapt is None else adapt,
                run_command=docker,
            )

    def diagnostic(self, docker: FakeDocker, **kwargs):
        with self.assertRaises(lifecycle.ModelContainerError) as raised:
            self.execute(docker, **kwargs)
        self.assertNotIn("private prompt", str(raised.exception))
        diagnostic = raised.exception.diagnostic
        json.dumps(diagnostic.to_dict())
        self.assertTrue(diagnostic.message)
        return diagnostic

    def test_attach_envelope_covers_existing_sequential_executor_limits(self):
        executor = Path(__file__).resolve().parents[2] / "runtime/goal1_hermetic_executor/main.go"
        source = executor.read_text(encoding="utf-8")
        self.assertRegex(source, r"llamaServerReadyTimeout\s*= 120 \* time.Second")
        self.assertRegex(source, r"llamaServerRequestTimeout\s*= 300 \* time.Second")
        self.assertGreater(lifecycle.MODEL_TIMEOUT_SECONDS, 120 + 300)
        self.assertEqual(450, lifecycle.MODEL_TIMEOUT_SECONDS)
        self.assertEqual(580, lifecycle.MODEL_LIFECYCLE_BUDGET_SECONDS)
        # An observed attach at the combined inner limit must survive the host.
        docker = FakeDocker()
        docker.attach_seconds = 420
        result = self.execute(docker)
        self.assertEqual(420_000, result.elapsed_milliseconds)
        self.assertTrue(result.teardown_observed)

    def test_success_interface_and_security_create_flags_are_preserved(self):
        docker = FakeDocker()
        result = self.execute(docker)
        self.assertIsInstance(result, lifecycle.ModelContainerObservation)
        self.assertEqual(b"raw completion", result.raw_completion)
        self.assertEqual(b"adapted completion", result.completion)
        self.assertEqual("ADAPTED", result.adaptation_rule)
        self.assertEqual(IMAGE_ID, result.image_id)
        create = docker.calls[1][0]
        name_index = create.index("--name")
        self.assertRegex(create[name_index + 1], r"^supernova-pilot-[0-9a-f]{32}$")
        original_flags = create[:name_index] + create[name_index + 2:]
        self.assertEqual([
            "docker", "create", "--pull", "never", "--network", "none",
            "--read-only", "--init", "--cap-drop", "ALL", "--security-opt",
            "no-new-privileges:true", "--pids-limit", "256", "--ipc", "none",
            "--user", "65532:65532", "--memory", "4294967296", "--cpus", "2",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=268435456",
            "--interactive", "frozen-image", "/opt/supernova/executor", "--stdio",
        ], original_flags)
        attach = next(call for call in docker.calls if call[0][1] == "start")
        self.assertEqual((450, b"private prompt"), attach[1:])
        self.assertEqual(["docker", "rm", "--force", create[name_index + 1]], docker.calls[-1][0])
        self.assertLessEqual(sum(call[1] for call in docker.calls), 580)

    def test_attach_timeout_preserves_partial_outputs_and_state_before_cleanup(self):
        docker = FakeDocker()
        stdout, stderr = b"partial frame" * 1000, b"\x1b[31mloader\xff" * 1000
        docker.faults["MODEL_ATTACH"] = subprocess.TimeoutExpired(
            ["docker", "start"], 450, output=stdout, stderr=stderr,
        )
        result = self.diagnostic(docker)
        self.assertEqual(("MODEL_ATTACH", "TIMEOUT"), (result.failure_stage, result.failure_kind))
        self.assertEqual(450_000, result.attach_elapsed_milliseconds)
        self.assertEqual("running", result.docker_state["Status"])
        self.assertEqual("FAILURE_INSPECT", result.docker_state_observed_stage)
        self.assertTrue(result.teardown_observed)
        attach = next(command for command in result.commands if command.stage == "MODEL_ATTACH")
        for captured, raw in ((attach.stdout, stdout), (attach.stderr, stderr)):
            self.assertEqual(len(raw), captured.byte_count)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), captured.sha256)
            self.assertLessEqual(len(captured.excerpt.encode("utf-8")), 4096)
            self.assertTrue(captured.excerpt_truncated)
        self.assertNotIn("\x1b", attach.stderr.excerpt)
        self.assertEqual(["FAILURE_INSPECT", "TEARDOWN"], [c.stage for c in result.commands[-2:]])
        self.assertEqual("NOT_OBSERVED_BY_FROZEN_EXECUTOR", result.to_dict()["timeout_budget"]["internal_phase_timing"])

    def test_executor_nonzero_is_an_error_with_output_and_exit_state(self):
        docker = FakeDocker()
        docker.faults["MODEL_ATTACH"] = (2, b"unfinished", b"generation failed: deadline exceeded")
        result = self.diagnostic(docker)
        self.assertEqual("COMMAND_FAILED", result.failure_kind)
        attach = next(c for c in result.commands if c.stage == "MODEL_ATTACH")
        self.assertEqual(2, attach.returncode)
        self.assertIn("deadline exceeded", attach.stderr.excerpt)
        self.assertTrue(result.teardown_observed)

    def test_image_inspect_failure_is_diagnostic_without_container_cleanup(self):
        for fault in (
            FileNotFoundError("docker absent"),
            subprocess.TimeoutExpired(["docker", "image", "inspect"], 30),
            (1, b"", b"image absent"),
            (0, b"[null]", b""),
            (0, b'[{"Id":"sha256:invalid"}]', b""),
        ):
            with self.subTest(fault=fault):
                docker = FakeDocker()
                docker.faults["IMAGE_INSPECT"] = fault
                result = self.diagnostic(docker)
                self.assertEqual("IMAGE_INSPECT", result.failure_stage)
                self.assertIsNone(result.container_name)
                self.assertFalse(result.teardown_observed)
                self.assertEqual(1, len(docker.calls))

    def test_create_failure_and_timeout_cleanup_by_generated_name(self):
        for fault in (
            subprocess.TimeoutExpired(["docker", "create"], 30, output=b""),
            OSError("daemon unavailable"),
            (1, b"", b"creation rejected"),
            (0, b"not an id", b""),
        ):
            with self.subTest(fault=fault):
                docker = FakeDocker()
                docker.faults["CONTAINER_CREATE"] = fault
                result = self.diagnostic(docker)
                self.assertEqual("CONTAINER_CREATE", result.failure_stage)
                self.assertIsNone(result.container_id)
                self.assertTrue(result.teardown_observed)
                self.assertEqual(result.container_name, docker.calls[-1][0][-1])

    def test_security_inspect_failures_stop_before_attach_and_cleanup(self):
        for fault in (
            subprocess.TimeoutExpired(["docker", "inspect"], 30),
            (1, b"", b"inspect failed"),
            (0, b"not JSON", b""),
        ):
            with self.subTest(fault=fault):
                docker = FakeDocker()
                docker.faults["SECURITY_INSPECT"] = fault
                result = self.diagnostic(docker)
                self.assertEqual("SECURITY_INSPECT", result.failure_stage)
                self.assertFalse(any(call[0][1] == "start" for call in docker.calls))
                self.assertTrue(result.teardown_observed)

    def test_security_drift_stops_before_attach_and_cleans_up(self):
        docker = FakeDocker()
        docker.security_drift = True
        result = self.diagnostic(docker)
        self.assertIn("security configuration drifted", result.message)
        self.assertTrue(result.teardown_observed)
        self.assertFalse(any(call[0][1] == "start" for call in docker.calls))

    def test_parse_and_adaptation_errors_preserve_completed_frame_and_cleanup(self):
        def fail(raw):
            raise ValueError("rejected output")

        for kwargs, stage in (({"parse": fail}, "RESPONSE_PARSE"), ({"adapt": fail}, "RESPONSE_ADAPT")):
            with self.subTest(stage=stage):
                docker = FakeDocker()
                result = self.diagnostic(docker, **kwargs)
                self.assertEqual(stage, result.failure_stage)
                self.assertTrue(result.teardown_observed)
                attach = next(c for c in result.commands if c.stage == "MODEL_ATTACH")
                self.assertEqual("frame", attach.stdout.excerpt)

    def test_failure_inspection_and_cleanup_failures_preserve_primary_timeout(self):
        docker = FakeDocker()
        docker.faults.update({
            "MODEL_ATTACH": subprocess.TimeoutExpired(["docker", "start"], 450, stderr=b"partial"),
            "FAILURE_INSPECT": subprocess.TimeoutExpired(["docker", "inspect"], 10),
            "TEARDOWN": subprocess.TimeoutExpired(["docker", "rm"], 30),
        })
        result = self.diagnostic(docker)
        self.assertEqual("MODEL_ATTACH", result.failure_stage)
        self.assertEqual("TIMEOUT", result.failure_kind)
        self.assertTrue(result.docker_state_error)
        self.assertFalse(result.teardown_observed)
        self.assertTrue(result.teardown_error)
        self.assertEqual("created", result.docker_state["Status"])
        self.assertEqual("SECURITY_INSPECT", result.docker_state_observed_stage)
        self.assertEqual(580, sum(call[1] for call in docker.calls))

    def test_cleanup_failure_cannot_become_success(self):
        for fault in (
            subprocess.TimeoutExpired(["docker", "rm"], 30),
            OSError("cannot launch removal"),
            (1, b"", b"removal rejected"),
        ):
            with self.subTest(fault=fault):
                docker = FakeDocker()
                docker.faults["TEARDOWN"] = fault
                result = self.diagnostic(docker)
                self.assertEqual("TEARDOWN", result.failure_stage)
                self.assertFalse(result.teardown_observed)
                self.assertTrue(result.teardown_error)
                self.assertEqual("FAILURE_INSPECT", result.docker_state_observed_stage)
                self.assertEqual("running", result.docker_state["Status"])
                self.assertEqual(580, sum(call[1] for call in docker.calls))

    def test_cleanup_is_attempted_on_interruption(self):
        docker = FakeDocker()

        def interrupt(raw):
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            self.execute(docker, parse=interrupt)
        self.assertEqual("rm", docker.calls[-1][0][1])


if __name__ == "__main__":
    unittest.main()
