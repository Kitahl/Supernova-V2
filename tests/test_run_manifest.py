from __future__ import annotations

from collections import defaultdict
import json
import sys
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.artifacts import (
    ScheduledChatArtifactEnvelope,
    ScheduledChatArtifactKind,
)
from supernova_goal1.contracts import Arm
from supernova_goal1.execution.common import FrozenProblemRequest
from supernova_goal1.problem import BenchmarkProblemIdentity
from supernova_goal1.run_manifest import (
    PILOT_MANIFEST_PURPOSE,
    FrozenPilotProblem,
    PilotManifestItem,
    PilotOperatorRevealMap,
    PilotRevealEntry,
    PilotRunManifest,
    generate_seeded_paired_pilot_manifest,
)


HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64
HEX_D = "d" * 64
SEED_A = bytes.fromhex("11" * 32)
SEED_B = bytes.fromhex("22" * 32)


def frozen_problem(index: int, *, family: str | None = None) -> FrozenPilotProblem:
    return FrozenPilotProblem(
        problem=BenchmarkProblemIdentity(
            benchmark="miniF2F-Lean4-Kimina-composite",
            version="deepseek-v1.5-2c4ba911+kimina-5def318",
            split="validation",
            native_id=f"problem-{index:03d}",
        ),
        problem_sha256=f"{index:064x}",
        family_id=family or f"family-{index:03d}",
    )


def make_plan(
    *,
    seed: bytes = SEED_A,
    attempts_per_cell: int = 2,
    problems: tuple[FrozenPilotProblem, ...] | None = None,
) -> tuple[PilotRunManifest, PilotOperatorRevealMap]:
    with patch("supernova_goal1.run_manifest.token_bytes", return_value=seed):
        return generate_seeded_paired_pilot_manifest(
            analysis_id="goal1-discordance-pilot-v1",
            run_id="pilot-run-001",
            experiment_id="goal1-bootstrap-dry-run",
            problems=problems
            or tuple(frozen_problem(index) for index in range(1, 4)),
            benchmark_root_sha256=HEX_A,
            budget_id="pilot-budget-v1",
            budget_sha256=HEX_B,
            model_usage_basis="visible_utf8_bytes",
            runtime_sha256=HEX_C,
            attempts_per_cell=attempts_per_cell,
        )


def request_for(
    manifest: PilotRunManifest,
    reveal: PilotOperatorRevealMap,
    *,
    evaluation_id: str,
    attempt: int,
    arm: Arm | None = None,
) -> FrozenProblemRequest:
    item = next(
        item for item in manifest.items if item.evaluation_id == evaluation_id
    )
    reveal_entry = next(
        entry for entry in reveal.entries if entry.evaluation_id == evaluation_id
    )
    request_arm = arm or reveal_entry.arm
    artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
        "prove the frozen theorem",
        kind=ScheduledChatArtifactKind.REQUEST,
        run_id=manifest.run_id,
        problem_id=item.problem_id,
        arm=request_arm,
        attempt=attempt,
    )
    return FrozenProblemRequest(
        run_id=manifest.run_id,
        experiment_id=manifest.experiment_id,
        problem=item.problem,
        benchmark_root_sha256=manifest.benchmark_root_sha256,
        problem_sha256=item.problem_sha256,
        arm=request_arm,
        attempt=attempt,
        budget_id=manifest.budget_id,
        budget_sha256=manifest.budget_sha256,
        model_usage_basis=manifest.model_usage_basis,
        runtime_sha256=manifest.runtime_sha256,
        request_artifact=artifact,
    )


class PilotManifestGenerationTests(unittest.TestCase):
    def test_plan_is_reproducible_paired_and_non_credit(self) -> None:
        first_manifest, first_reveal = make_plan()
        second_manifest, second_reveal = make_plan()

        self.assertEqual(first_manifest, second_manifest)
        self.assertEqual(first_reveal, second_reveal)
        self.assertEqual(PILOT_MANIFEST_PURPOSE, first_manifest.purpose)
        self.assertEqual(15, len(first_manifest.items))
        self.assertEqual(15, len(first_reveal.entries))
        self.assertEqual(
            list(range(15)),
            [item.evaluation_index for item in first_manifest.items],
        )
        self.assertEqual({2}, {item.attempt_quota for item in first_manifest.items})
        self.assertEqual(
            first_manifest.manifest_sha256, first_reveal.manifest_sha256
        )
        first_reveal.validate_for(first_manifest)

        public_by_id = {
            item.evaluation_id: item for item in first_manifest.items
        }
        arms_by_problem: dict[str, set[Arm]] = defaultdict(set)
        for entry in first_reveal.entries:
            arms_by_problem[public_by_id[entry.evaluation_id].problem_id].add(
                entry.arm
            )
        self.assertEqual(
            {item.problem_id: set(Arm) for item in first_manifest.items},
            arms_by_problem,
        )

    def test_public_manifest_has_no_arm_or_assignment_authority(self) -> None:
        manifest, reveal = make_plan()
        self.assertIsInstance(manifest, PilotRunManifest)
        self.assertIsInstance(reveal, PilotOperatorRevealMap)
        self.assertIsNot(manifest, reveal)
        self.assertTrue(all(not hasattr(item, "arm") for item in manifest.items))
        self.assertTrue(
            all(not hasattr(item, "assignment_id") for item in manifest.items)
        )

        public = manifest.to_mapping()
        serialized = json.dumps(public, sort_keys=True)
        self.assertNotIn('"arm"', serialized)
        self.assertNotIn("assignment_id", serialized)
        self.assertNotIn(SEED_A.hex(), serialized)
        self.assertNotIn("reveal", public)
        self.assertNotIn("entries", public)

        operator = reveal.to_mapping()
        self.assertIn("entries", operator)
        self.assertEqual(SEED_A.hex(), operator["seed_hex"])
        self.assertTrue(all("arm" in item for item in operator["entries"]))
        self.assertTrue(
            all("assignment_id" in item for item in operator["entries"])
        )

    def test_seed_changes_order_and_blind_labels_not_frozen_problem_set(self) -> None:
        first_manifest, first_reveal = make_plan(seed=SEED_A)
        second_manifest, second_reveal = make_plan(seed=SEED_B)
        self.assertNotEqual(
            first_manifest.reveal_commitment_sha256,
            second_manifest.reveal_commitment_sha256,
        )
        self.assertNotEqual(first_manifest.items, second_manifest.items)
        self.assertNotEqual(first_reveal.entries, second_reveal.entries)
        self.assertEqual(
            {item.problem_id for item in first_manifest.items},
            {item.problem_id for item in second_manifest.items},
        )

    def test_problem_input_order_does_not_change_plan(self) -> None:
        problems = tuple(frozen_problem(index) for index in range(1, 4))
        forward = make_plan(problems=problems)
        backward = make_plan(problems=tuple(reversed(problems)))
        self.assertEqual(forward, backward)

    def test_round_trip_and_hash_tamper_detection(self) -> None:
        manifest, reveal = make_plan()
        self.assertEqual(
            manifest, PilotRunManifest.from_mapping(manifest.to_mapping())
        )
        self.assertEqual(
            reveal, PilotOperatorRevealMap.from_mapping(reveal.to_mapping())
        )

        tampered_manifest = manifest.to_mapping()
        tampered_manifest["budget_id"] = "different-budget"
        with self.assertRaisesRegex(ValueError, "manifest_sha256"):
            PilotRunManifest.from_mapping(tampered_manifest)

        tampered_reveal = reveal.to_mapping()
        original_arm = Arm(tampered_reveal["entries"][0]["arm"])
        tampered_reveal["entries"][0]["arm"] = next(
            arm.value for arm in Arm if arm is not original_arm
        )
        with self.assertRaisesRegex(ValueError, "reveal_sha256"):
            PilotOperatorRevealMap.from_mapping(tampered_reveal)

    def test_reveal_is_bound_to_exact_public_manifest(self) -> None:
        manifest_a, reveal_a = make_plan(seed=SEED_A)
        manifest_b, _ = make_plan(seed=SEED_B)
        reveal_a.validate_for(manifest_a)
        with self.assertRaisesRegex(ValueError, "does not bind"):
            reveal_a.validate_for(manifest_b)

    def test_fully_rehashed_arm_substitution_breaks_manifest_commitment(self) -> None:
        manifest, reveal = make_plan(problems=(frozen_problem(1),))
        public_by_id = {item.evaluation_id: item for item in manifest.items}
        entries = list(reveal.entries)
        same_problem = [
            index
            for index, entry in enumerate(entries)
            if public_by_id[entry.evaluation_id].problem_id
            == manifest.items[0].problem_id
        ]
        first_index, second_index = same_problem[:2]
        first = entries[first_index]
        second = entries[second_index]
        entries[first_index] = PilotRevealEntry(
            evaluation_id=first.evaluation_id,
            assignment_id=second.assignment_id,
            arm=second.arm,
            execution_index=second.execution_index,
            attempt_quota=first.attempt_quota,
        )
        entries[second_index] = PilotRevealEntry(
            evaluation_id=second.evaluation_id,
            assignment_id=first.assignment_id,
            arm=first.arm,
            execution_index=first.execution_index,
            attempt_quota=second.attempt_quota,
        )
        forged = PilotOperatorRevealMap(
            manifest_sha256=manifest.manifest_sha256,
            seed_hex=reveal.seed_hex,
            entries=tuple(entries),
        )
        self.assertNotEqual(reveal.reveal_sha256, forged.reveal_sha256)
        with self.assertRaisesRegex(ValueError, "reveal commitment"):
            forged.validate_for(manifest)

    def test_seed_is_owned_by_exact_32_byte_csprng_boundary(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "exactly 32 bytes"):
            make_plan(seed=b"too-short")
        with self.assertRaisesRegex(RuntimeError, "exactly 32 bytes"):
            make_plan(seed="x" * 32)  # type: ignore[arg-type]

    def test_request_validation_enforces_exact_cell_and_attempt_quota(self) -> None:
        manifest, reveal = make_plan(attempts_per_cell=2)
        evaluation_id = manifest.items[0].evaluation_id
        reveal.validate_request(
            manifest,
            evaluation_id=evaluation_id,
            request=request_for(
                manifest, reveal, evaluation_id=evaluation_id, attempt=0
            ),
        )
        reveal.validate_request(
            manifest,
            evaluation_id=evaluation_id,
            request=request_for(
                manifest, reveal, evaluation_id=evaluation_id, attempt=1
            ),
        )
        with self.assertRaisesRegex(ValueError, "exceeds the frozen pilot quota"):
            reveal.validate_request(
                manifest,
                evaluation_id=evaluation_id,
                request=request_for(
                    manifest, reveal, evaluation_id=evaluation_id, attempt=2
                ),
            )

        actual_arm = next(
            entry.arm
            for entry in reveal.entries
            if entry.evaluation_id == evaluation_id
        )
        wrong_arm = next(arm for arm in Arm if arm is not actual_arm)
        with self.assertRaisesRegex(ValueError, "does not match the frozen pilot cell"):
            reveal.validate_request(
                manifest,
                evaluation_id=evaluation_id,
                request=request_for(
                    manifest,
                    reveal,
                    evaluation_id=evaluation_id,
                    attempt=0,
                    arm=wrong_arm,
                ),
            )
        with self.assertRaisesRegex(ValueError, "not present"):
            reveal.validate_request(
                manifest,
                evaluation_id="eval-not-in-manifest",
                request=request_for(
                    manifest, reveal, evaluation_id=evaluation_id, attempt=0
                ),
            )

    def test_attempt_quota_is_positive_and_symmetric(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive integer"):
            make_plan(attempts_per_cell=0)

        manifest, _ = make_plan()
        mismatched = list(manifest.items)
        item = mismatched[0]
        mismatched[0] = PilotManifestItem(
            evaluation_id=item.evaluation_id,
            problem=item.problem,
            problem_sha256=item.problem_sha256,
            family_id=item.family_id,
            evaluation_index=item.evaluation_index,
            attempt_quota=item.attempt_quota + 1,
        )
        with self.assertRaisesRegex(ValueError, "symmetric"):
            PilotRunManifest(
                analysis_id=manifest.analysis_id,
                run_id=manifest.run_id,
                experiment_id=manifest.experiment_id,
                benchmark_root_sha256=manifest.benchmark_root_sha256,
                budget_id=manifest.budget_id,
                budget_sha256=manifest.budget_sha256,
                model_usage_basis=manifest.model_usage_basis,
                runtime_sha256=manifest.runtime_sha256,
                reveal_commitment_sha256=manifest.reveal_commitment_sha256,
                items=tuple(mismatched),
            )

    def test_duplicate_family_and_cross_split_inputs_fail_closed(self) -> None:
        duplicate_family = (
            frozen_problem(1, family="same-family"),
            frozen_problem(2, family="same-family"),
        )
        with self.assertRaisesRegex(ValueError, "one problem per family"):
            make_plan(problems=duplicate_family)

        other_split = FrozenPilotProblem(
            problem=BenchmarkProblemIdentity(
                benchmark="miniF2F-Lean4-Kimina-composite",
                version="deepseek-v1.5-2c4ba911+kimina-5def318",
                split="test",
                native_id="problem-002",
            ),
            problem_sha256=HEX_D,
            family_id="family-002",
        )
        with self.assertRaisesRegex(ValueError, "one benchmark/version/split"):
            make_plan(problems=(frozen_problem(1), other_split))

    def test_frozen_problem_and_manifest_snapshot_caller_values(self) -> None:
        source = frozen_problem(1)
        manifest, _ = make_plan(problems=(source,))
        self.assertIsNot(source, manifest.items[0])
        self.assertEqual(source.problem, manifest.items[0].problem)
        self.assertIsNot(source.problem, manifest.items[0].problem)

    def test_public_item_count_cannot_masquerade_as_complete_pairing(self) -> None:
        manifest, _ = make_plan(problems=(frozen_problem(1),))
        with self.assertRaisesRegex(ValueError, "exactly one cell per arm"):
            PilotRunManifest(
                analysis_id=manifest.analysis_id,
                run_id=manifest.run_id,
                experiment_id=manifest.experiment_id,
                benchmark_root_sha256=manifest.benchmark_root_sha256,
                budget_id=manifest.budget_id,
                budget_sha256=manifest.budget_sha256,
                model_usage_basis=manifest.model_usage_basis,
                runtime_sha256=manifest.runtime_sha256,
                reveal_commitment_sha256=manifest.reveal_commitment_sha256,
                items=manifest.items[:-1],
            )

    def test_reveal_rejects_duplicate_arm_for_one_problem(self) -> None:
        manifest, reveal = make_plan(problems=(frozen_problem(1),))
        entries = list(reveal.entries)
        changed = entries[1]
        entries[1] = PilotRevealEntry(
            evaluation_id=changed.evaluation_id,
            assignment_id=changed.assignment_id,
            arm=entries[0].arm,
            execution_index=changed.execution_index,
            attempt_quota=changed.attempt_quota,
        )
        malformed = PilotOperatorRevealMap(
            manifest_sha256=manifest.manifest_sha256,
            seed_hex=reveal.seed_hex,
            entries=tuple(entries),
        )
        with self.assertRaisesRegex(ValueError, "reveal commitment"):
            malformed.validate_for(manifest)

    def test_manifest_types_do_not_expose_combined_reveal_field(self) -> None:
        manifest, reveal = make_plan()
        self.assertNotIn("reveal", manifest._fields)
        self.assertNotIn("entries", manifest._fields)
        self.assertNotIn("items", reveal._fields)
        self.assertFalse(hasattr(manifest, "arm"))
        self.assertFalse(hasattr(manifest, "reveal_sha256"))
        self.assertEqual(
            ("manifest_sha256", "seed_hex", "entries"),
            PilotOperatorRevealMap._fields,
        )


if __name__ == "__main__":
    unittest.main()
