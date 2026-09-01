from __future__ import annotations

import hashlib
import json
import pathlib
import unittest

from supernova_goal1.confirmatory_supervisor import (
    load_repository_execution_bindings,
)
from supernova_goal1.execution_authority import canonical_sha256

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "runtime" / "goal1_hermetic_executor"
WORKFLOW = ROOT / ".github" / "workflows" / "goal1_hermetic_executor.yml"


class HermeticExecutorBuildTests(unittest.TestCase):
    def test_build_lock_is_exact_and_content_addressed(self) -> None:
        lock = json.loads((CONTEXT / "BUILD_LOCK.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["schema"], "supernova.hermetic-executor-build-lock.v1")
        self.assertEqual(lock["platform"], "linux/amd64")
        self.assertIn("@sha256:", lock["builder_image"])
        self.assertIn("@sha256:", lock["runtime_image"])
        self.assertEqual(
            lock["model"]["sha256"],
            "f2d0fdabe97814b7d3fe1fec4a6dd32418c081b6953882ec31d3352f0058b65d",
        )
        self.assertEqual(
            lock["model"]["upstream_exact_version"],
            "AI-MO/Kimina-Prover-Preview-Distill-1.5B@"
            "737ff8442bdd883bf18c9b76f46f8e63d03949d1",
        )
        self.assertEqual(lock["command"], ["/opt/supernova/executor", "--stdio"])
        self.assertEqual(lock["working_directory"], "/app")
        self.assertEqual(lock["container_user"], "65532:65532")
        self.assertEqual(lock["generation_settings"]["device"], "none")

    def test_build_lock_matches_dockerfile_and_wrapper(self) -> None:
        lock = json.loads((CONTEXT / "BUILD_LOCK.json").read_text(encoding="utf-8"))
        dockerfile = (CONTEXT / "Dockerfile").read_text(encoding="utf-8")
        wrapper = (CONTEXT / "main.go").read_text(encoding="utf-8")
        self.assertIn(f"FROM {lock['builder_image']} AS wrapper-builder", dockerfile)
        self.assertEqual(dockerfile.count(f"FROM {lock['runtime_image']}"), 2)
        revision = lock["model"]["exact_version"].split("@", 1)[1].split("#", 1)[0]
        self.assertIn(revision, dockerfile)
        self.assertIn(lock["model"]["file"], dockerfile)
        self.assertIn(lock["model"]["sha256"], dockerfile)
        self.assertIn('CMD ["/opt/supernova/executor", "--stdio"]', dockerfile)
        self.assertIn(f"WORKDIR {lock['working_directory']}", dockerfile)
        settings = lock["generation_settings"]
        expected_fragments = (
            f'MaxTokens: {settings["max_output_tokens"]}',
            f'"--ctx-size", "{settings["context_tokens"]}"',
            f'"--threads", "{settings["cpu_threads"]}"',
            f'"--batch-size", "{settings["batch_size"]}"',
            f'Seed:        {settings["seed"]}',
            f'Temperature: {settings["temperature"]:.2f}',
            f'TopK:        {settings["top_k"]}',
            f'TopP:        {settings["top_p"]}',
            settings["model_alias"],
            settings["system_prompt"],
        )
        for fragment in expected_fragments:
            self.assertIn(fragment, wrapper)
        self.assertNotIn('"--single-turn"', wrapper)
        self.assertNotIn('"--prompt"', wrapper)
        self.assertIn('"--jinja"', wrapper)
        self.assertIn('"/v1/chat/completions"', wrapper)
        self.assertEqual(settings["engine"], "llama-server")
        self.assertEqual(settings["engine_path"], "/app/llama-server")
        self.assertEqual(
            settings["interface"],
            "LOOPBACK_HTTP_JSON_POST_V1_CHAT_COMPLETIONS_ONE_MESSAGE_CONTENT_FIELD",
        )

    def test_dockerfile_has_no_mutable_runtime_or_secret_input(self) -> None:
        dockerfile = (CONTEXT / "Dockerfile").read_text(encoding="utf-8")
        self.assertEqual(dockerfile.count("FROM "), 3)
        self.assertEqual(dockerfile.count("@sha256:"), 3)
        self.assertNotIn("ARG ", dockerfile)
        self.assertNotIn("--mount=type=secret", dockerfile)
        self.assertIn("USER 65532:65532", dockerfile)
        self.assertIn("WORKDIR /app", dockerfile)
        self.assertNotIn("WORKDIR /opt/supernova", dockerfile)
        self.assertIn('ENTRYPOINT []', dockerfile)
        self.assertIn('CMD ["/opt/supernova/executor", "--stdio"]', dockerfile)
        self.assertIn("sha256sum --check --strict", dockerfile)
        self.assertIn("COPY BUILD_LOCK.json /opt/supernova/BUILD_LOCK.json", dockerfile)
        self.assertIn("test -x /app/llama-server", dockerfile)
        self.assertIn("server-b10666@sha256:5c8d0ec9d1d9c1f302e2c30071f8eb89456808306ececc381480fb9e90d7e8c1", dockerfile)
        self.assertIn("/app/llama-server --version", dockerfile)
        self.assertFalse(dockerfile.startswith("# syntax="))

    def test_workflow_builds_only_narrow_context_and_never_receives_signing_keys(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("context: runtime/goal1_hermetic_executor", workflow)
        self.assertIn("platforms: linux/amd64", workflow)
        self.assertIn("--network none", workflow)
        self.assertIn("timeout --signal=KILL 180s docker run", workflow)
        self.assertIn('--cidfile "$cidfile"', workflow)
        self.assertIn("trap cleanup EXIT", workflow)
        self.assertIn('docker rm -f "$(cat "$cidfile")"', workflow)
        self.assertIn("push: false", workflow)
        self.assertIn("github.event_name != 'pull_request'", workflow)
        self.assertEqual(workflow.count("docker/build-push-action@"), 1)
        self.assertIn("docker push", workflow)
        self.assertIn('test "$candidate_id" = "$published_id"', workflow)
        self.assertIn("build_lock_sha256", workflow)
        self.assertNotIn("ROOT_PRIVATE", workflow)
        self.assertNotIn("RECEIPT_PRIVATE", workflow)
        self.assertNotIn("CONFIRMATORY_EXECUTION_AUTHORITY", workflow)
        for action in (
            "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
            "docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f",
            "docker/build-push-action@10e90e3645eae34f1e60eeb005ba3a3d33f178e8",
        ):
            self.assertIn(action, workflow)

    def test_public_launcher_and_capacity_bind_published_executor(self) -> None:
        publication = json.loads(
            (CONTEXT / "PUBLISHED_IMAGE.json").read_text(encoding="utf-8")
        )
        lock_raw = (CONTEXT / "BUILD_LOCK.json").read_bytes()
        lock = json.loads(lock_raw)
        if publication["build_lock_sha256"] != hashlib.sha256(lock_raw).hexdigest():
            with self.assertRaisesRegex(ValueError, "different build lock"):
                load_repository_execution_bindings(ROOT)
            return
        launcher, capacity = load_repository_execution_bindings(ROOT)
        runtime = json.loads(
            (ROOT / "goal1" / "CONFIRMATORY_RUNTIME.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            launcher.container_image_ref,
            "ghcr.io/kitahl/supernova-goal1-executor@sha256:"
            "db481d797c97a2edaaa1439e368209b174cdca9585474093defdcea1690c54b7",
        )
        self.assertEqual(
            launcher.inference_runtime_sha256,
            "bab9d9c7aa177608100060782b0386f5d28b34a27aa3515ba6e54ef86b47fc67",
        )
        self.assertEqual(launcher.exact_model_version, lock["model"]["exact_version"])
        self.assertEqual(launcher.model_weights_sha256, lock["model"]["sha256"])
        self.assertEqual(launcher.tokenizer_sha256, lock["model"]["sha256"])
        self.assertEqual(launcher.generation_settings, lock["generation_settings"])
        self.assertEqual(capacity["executor_image_ref"], launcher.container_image_ref)
        self.assertEqual(publication["image_ref"], launcher.container_image_ref)
        self.assertEqual(
            publication["source_commit"],
            "7cd2f582904cd82eb19a655bd575f29ab8415518",
        )
        self.assertEqual(publication["workflow_run_id"], 33231696180)
        self.assertEqual(
            publication["build_lock_sha256"],
            "f026c814eff1efe1f9de908a59914948aa8c4b496d94c86b1b98a4394231fa85",
        )
        self.assertEqual(
            publication["evidence_artifact_digest"],
            "sha256:ea59971aa3e3476eb09f3e0aeb145a2dcf84ce13a37f3b36fdd82e444530fe3e",
        )
        self.assertEqual(
            capacity["launcher_artifact_sha256"],
            "83c8fb2f180acd711647b2d0279dd2449804638d2a41b40d6bf81a34d0b37587",
        )
        self.assertEqual(
            canonical_sha256(capacity),
            "697a0a254714601dbe821af72a73e2ba54e823e6bc72bc8d1a4dab2d20060e11",
        )
        self.assertEqual(capacity["verifier_slot"], runtime["resource_limits"])
        self.assertEqual(capacity["model_slot"]["memory_bytes"], 4_294_967_296)
        self.assertEqual(capacity["model_slot"]["nano_cpus"], 2_000_000_000)
        self.assertEqual(capacity["model_slot"]["network"], "none")
        self.assertEqual(capacity["model_slot"]["gpu_device_requests"], 0)

    def test_board_declares_every_new_path(self) -> None:
        board = json.loads((ROOT / "orchestration" / "BOARD.json").read_text(encoding="utf-8"))
        archive = json.loads(
            (ROOT / "orchestration" / "ARCHIVE.json").read_text(encoding="utf-8")
        )
        ticket = next(
            item
            for item in board["tickets"] + archive["completed_tickets"]
            if item["id"] == "G1-121"
        )
        for path in (
            ".github/workflows/goal1_hermetic_executor.yml",
            "runtime/goal1_hermetic_executor/",
            "goal1/CONFIRMATORY_EXECUTION_AUTHORITY.json",
            "goal1/CONFIRMATORY_EXECUTOR_LAUNCHER.json",
            "goal1/CONFIRMATORY_CAPACITY_BINDING.json",
            "goal1/CONFIRMATORY_TRUST_ROOT.json",
            "tests/test_goal1_hermetic_executor.py",
        ):
            self.assertIn(path, ticket["paths"])
        self.assertEqual(ticket["status"], "DONE")
        self.assertEqual(ticket["phase_two"]["preflight_validation_verdict"], "PASS")
        self.assertIsNone(ticket["phase_two"]["remaining_requirement"])
        self.assertEqual(ticket["completion"]["pull_request"], 86)


if __name__ == "__main__":
    unittest.main()
