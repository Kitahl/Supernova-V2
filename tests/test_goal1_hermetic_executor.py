from __future__ import annotations

import json
import pathlib
import unittest

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
            "1d9614638d18024d0fbb36575a15f1302a3adf044df10345688ec4f6e1c4ff32",
        )
        self.assertEqual(lock["command"], ["/opt/supernova/executor", "--stdio"])
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
        settings = lock["generation_settings"]
        expected_fragments = (
            f'"-n", "{settings["max_output_tokens"]}"',
            f'"--ctx-size", "{settings["context_tokens"]}"',
            f'"--threads", "{settings["cpu_threads"]}"',
            f'"--batch-size", "{settings["batch_size"]}"',
            f'"--seed", "{settings["seed"]}"',
            f'"--temp", "{settings["temperature"]:.2f}"',
            f'"--top-k", "{settings["top_k"]}"',
            f'"--top-p", "{settings["top_p"]}"',
        )
        for fragment in expected_fragments:
            self.assertIn(fragment, wrapper)
        self.assertGreaterEqual(wrapper.count('"--single-turn"'), 2)
        self.assertNotIn('"-no-cnv"', wrapper)

    def test_dockerfile_has_no_mutable_runtime_or_secret_input(self) -> None:
        dockerfile = (CONTEXT / "Dockerfile").read_text(encoding="utf-8")
        self.assertEqual(dockerfile.count("FROM "), 3)
        self.assertEqual(dockerfile.count("@sha256:"), 3)
        self.assertNotIn("ARG ", dockerfile)
        self.assertNotIn("--mount=type=secret", dockerfile)
        self.assertIn("USER 65532:65532", dockerfile)
        self.assertIn('ENTRYPOINT []', dockerfile)
        self.assertIn('CMD ["/opt/supernova/executor", "--stdio"]', dockerfile)
        self.assertIn("sha256sum --check --strict", dockerfile)
        self.assertIn("COPY BUILD_LOCK.json /opt/supernova/BUILD_LOCK.json", dockerfile)
        self.assertIn("cp -P /app/*.so* /usr/local/lib/", dockerfile)
        self.assertIn("test -r /usr/local/lib/libllama-cli-impl.so", dockerfile)
        self.assertIn("/app/llama-cli --version", dockerfile)
        self.assertFalse(dockerfile.startswith("# syntax="))

    def test_workflow_builds_only_narrow_context_and_never_receives_signing_keys(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("context: runtime/goal1_hermetic_executor", workflow)
        self.assertIn("platforms: linux/amd64", workflow)
        self.assertIn("--network none", workflow)
        self.assertIn("timeout --signal=KILL 180s docker run", workflow)
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

    def test_board_declares_every_new_path(self) -> None:
        board = json.loads((ROOT / "orchestration" / "BOARD.json").read_text(encoding="utf-8"))
        ticket = next(item for item in board["tickets"] if item["id"] == "G1-121")
        for path in (
            ".github/workflows/goal1_hermetic_executor.yml",
            "runtime/goal1_hermetic_executor/",
            "goal1/CONFIRMATORY_EXECUTOR_LAUNCHER.json",
            "goal1/CONFIRMATORY_CAPACITY_BINDING.json",
            "tests/test_goal1_hermetic_executor.py",
        ):
            self.assertIn(path, ticket["paths"])
        self.assertEqual(ticket["status"], "READY")


if __name__ == "__main__":
    unittest.main()
