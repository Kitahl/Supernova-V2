"""Guard the bounded remote diagnostic independently of mocked Lean results."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReadinessWorkflowTests(unittest.TestCase):
    def test_only_exact_branch_push_can_start_diagnostic(self):
        workflow = (ROOT / ".github/workflows/goal1_readiness.yml").read_text()
        self.assertIn("branches: [work/PM/G1V2-readiness]", workflow)
        self.assertIn("  push:\n", workflow)
        for forbidden in (
            "pull_request:",
            "workflow_dispatch:",
            "schedule:",
            "workflow_run:",
        ):
            self.assertNotIn(forbidden, workflow)
        self.assertIn("timeout-minutes: 25", workflow)
        self.assertIn("cancel-in-progress: false", workflow)

    def test_no_build_model_or_write_credentials_in_runtime_step(self):
        workflow = (ROOT / ".github/workflows/goal1_readiness.yml").read_text()
        for forbidden in (
            "--executor-image",
            "docker build",
            "build-push-action",
            "write",
            "--review-evidence",
        ):
            self.assertNotIn(forbidden, workflow)
        runtime = workflow.split("      - name: Replay five saved candidates", 1)[
            1
        ].split("      - name: Preserve", 1)[0]
        for forbidden in ("secrets.", "TOKEN", "env:"):
            self.assertNotIn(forbidden, runtime)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("trap 'docker logout ghcr.io' EXIT", workflow)
        self.assertIn('r["new_model_calls"] == 0', runtime)
        self.assertIn('r["canary_status"] == "NOT_REQUESTED"', runtime)

    def test_archive_and_image_match_existing_plan(self):
        workflow = (ROOT / ".github/workflows/goal1_readiness.yml").read_text()
        plan = json.loads(
            (ROOT / "integration/goal1_validation_pilot/REPAIR_PLAN.json").read_text()
        )
        self.assertIn(plan["verifier_image_ref"], workflow)
        self.assertIn(plan["historical_artifact"]["zip_sha256"], workflow)
        self.assertIn(
            f"artifacts/{plan['historical_artifact']['artifact_id']}/zip", workflow
        )
        self.assertIn("sha256sum --check --strict", workflow)


if __name__ == "__main__":
    unittest.main()
