from __future__ import annotations

import copy
import json
import random
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.evaluate import evaluate_experiment


class AdversarialEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = json.loads((ROOT / "goal1" / "GOAL1.json").read_text(encoding="utf-8"))
        cls.records = json.loads(
            (ROOT / "examples" / "dry_run_records.json").read_text(encoding="utf-8")
        )

    def frozen_spec(self) -> dict:
        spec = copy.deepcopy(self.spec)
        spec["cost_model_frozen"] = True
        return spec

    def test_identical_duplicate_problem_arm_is_rejected(self) -> None:
        for source in self.records:
            with self.subTest(problem_id=source["problem_id"], arm=source["arm"]):
                duplicate = copy.deepcopy(source)
                with self.assertRaisesRegex(
                    ValueError,
                    f"duplicate outcome for {source['problem_id']}/{source['arm']}",
                ):
                    evaluate_experiment(self.spec, [*self.records, duplicate])

    def test_conflicting_duplicate_problem_arm_is_rejected(self) -> None:
        for source in self.records:
            with self.subTest(problem_id=source["problem_id"], arm=source["arm"]):
                duplicate = copy.deepcopy(source)
                duplicate["solved"] = not source["solved"]
                duplicate["verifier_passed"] = duplicate["solved"]
                with self.assertRaisesRegex(
                    ValueError,
                    f"duplicate outcome for {source['problem_id']}/{source['arm']}",
                ):
                    evaluate_experiment(self.spec, [*self.records, duplicate])

    def test_missing_arm_for_each_problem_is_incomplete(self) -> None:
        spec = self.frozen_spec()
        for problem_id in self.spec["required_problem_ids"]:
            for arm in self.spec["arms"]:
                with self.subTest(problem_id=problem_id, arm=arm):
                    records = [
                        record
                        for record in self.records
                        if not (
                            record["problem_id"] == problem_id
                            and record["arm"] == arm
                        )
                    ]
                    result = evaluate_experiment(spec, records)
                    self.assertEqual("INCOMPLETE", result["decision"])
                    self.assertEqual([], result["pairwise"])
                    self.assertEqual(
                        [{"problem_id": problem_id, "arm": arm}],
                        result["missing"],
                    )

    def test_each_complete_cost_dimension_rejects_overrun(self) -> None:
        ceiling = self.spec["budget_ceiling"]
        for record_index, source in enumerate(self.records):
            for field, allowed in ceiling.items():
                with self.subTest(
                    problem_id=source["problem_id"], arm=source["arm"], field=field
                ):
                    records = copy.deepcopy(self.records)
                    records[record_index]["cost"][field] = allowed + 1
                    with self.assertRaisesRegex(
                        ValueError,
                        f"cost ceiling exceeded for {source['problem_id']}/{source['arm']}",
                    ):
                        evaluate_experiment(self.spec, records)

    def test_each_complete_cost_dimension_accepts_exact_ceiling(self) -> None:
        spec = self.frozen_spec()
        ceiling = self.spec["budget_ceiling"]
        for record_index, source in enumerate(self.records):
            for field, allowed in ceiling.items():
                with self.subTest(
                    problem_id=source["problem_id"], arm=source["arm"], field=field
                ):
                    records = copy.deepcopy(self.records)
                    records[record_index]["cost"][field] = allowed
                    result = evaluate_experiment(spec, records)
                    self.assertEqual(len(self.records), result["received_record_count"])

    def test_solved_without_verifier_pass_is_rejected_for_every_cell(self) -> None:
        for record_index, source in enumerate(self.records):
            with self.subTest(problem_id=source["problem_id"], arm=source["arm"]):
                records = copy.deepcopy(self.records)
                records[record_index]["solved"] = True
                records[record_index]["verifier_passed"] = False
                with self.assertRaisesRegex(ValueError, "requires verifier_passed=true"):
                    evaluate_experiment(self.spec, records)

    def test_record_identity_mismatches_are_rejected_for_every_cell(self) -> None:
        cases = (
            (
                "experiment_id",
                "other-experiment",
                "record experiment_id does not match the experiment spec",
            ),
            (
                "problem_id",
                "unexpected-problem",
                "unexpected problem_id: unexpected-problem",
            ),
            (
                "budget_id",
                "other-budget",
                "record budget_id does not match the frozen budget",
            ),
        )
        for record_index, source in enumerate(self.records):
            for field, value, message in cases:
                with self.subTest(
                    problem_id=source["problem_id"],
                    arm=source["arm"],
                    mismatched_field=field,
                ):
                    records = copy.deepcopy(self.records)
                    records[record_index][field] = value
                    with self.assertRaisesRegex(ValueError, message):
                        evaluate_experiment(self.spec, records)

    def test_malformed_record_types_are_rejected_for_every_cell(self) -> None:
        cases = (
            (
                "experiment_id_none",
                ("experiment_id", None),
                "experiment_id, problem_id, budget_id must be non-empty strings",
            ),
            (
                "problem_id_list",
                ("problem_id", []),
                "experiment_id, problem_id, budget_id must be non-empty strings",
            ),
            (
                "budget_id_empty",
                ("budget_id", ""),
                "experiment_id, problem_id, budget_id must be non-empty strings",
            ),
            (
                "solved_integer",
                ("solved", 1),
                "solved and verifier_passed must be boolean",
            ),
            (
                "verifier_string",
                ("verifier_passed", "true"),
                "solved and verifier_passed must be boolean",
            ),
            ("unknown_arm", ("arm", 17), "unknown arm"),
            ("cost_not_mapping", ("cost", []), "cost must be an object"),
        )
        for record_index, source in enumerate(self.records):
            for name, (field, value), message in cases:
                with self.subTest(
                    problem_id=source["problem_id"], arm=source["arm"], case=name
                ):
                    records = copy.deepcopy(self.records)
                    records[record_index][field] = value
                    with self.assertRaisesRegex(ValueError, message):
                        evaluate_experiment(self.spec, records)

    def test_each_cost_dimension_rejects_malformed_scalars_for_every_cell(self) -> None:
        bad_values = (True, -1, 1.5, "1")
        for record_index, source in enumerate(self.records):
            for field in self.spec["budget_ceiling"]:
                for value in bad_values:
                    with self.subTest(
                        problem_id=source["problem_id"],
                        arm=source["arm"],
                        field=field,
                        value=repr(value),
                    ):
                        records = copy.deepcopy(self.records)
                        records[record_index]["cost"][field] = value
                        with self.assertRaisesRegex(
                            ValueError,
                            rf"cost\.{field} must be a non-negative integer",
                        ):
                            evaluate_experiment(self.spec, records)

    def test_cost_shape_must_be_exact_for_every_cell(self) -> None:
        expected_message = "cost fields must be exactly"
        for record_index, source in enumerate(self.records):
            for field in self.spec["budget_ceiling"]:
                with self.subTest(
                    problem_id=source["problem_id"],
                    arm=source["arm"],
                    missing_field=field,
                ):
                    records = copy.deepcopy(self.records)
                    del records[record_index]["cost"][field]
                    with self.assertRaisesRegex(ValueError, expected_message):
                        evaluate_experiment(self.spec, records)

            with self.subTest(
                problem_id=source["problem_id"],
                arm=source["arm"],
                extra_field="untracked_cost",
            ):
                records = copy.deepcopy(self.records)
                records[record_index]["cost"]["untracked_cost"] = 0
                with self.assertRaisesRegex(ValueError, expected_message):
                    evaluate_experiment(self.spec, records)

    def test_budget_ceiling_shape_and_scalars_are_rejected(self) -> None:
        expected_shape_message = "budget_ceiling fields must be exactly"
        for field in self.spec["budget_ceiling"]:
            with self.subTest(missing_field=field):
                spec = copy.deepcopy(self.spec)
                del spec["budget_ceiling"][field]
                with self.assertRaisesRegex(ValueError, expected_shape_message):
                    evaluate_experiment(spec, self.records)

        spec = copy.deepcopy(self.spec)
        spec["budget_ceiling"]["untracked_cost"] = 0
        with self.assertRaisesRegex(ValueError, expected_shape_message):
            evaluate_experiment(spec, self.records)

        bad_values = (True, -1, 1.5, "1")
        for field in self.spec["budget_ceiling"]:
            for value in bad_values:
                with self.subTest(field=field, value=repr(value)):
                    spec = copy.deepcopy(self.spec)
                    spec["budget_ceiling"][field] = value
                    with self.assertRaisesRegex(
                        ValueError,
                        rf"budget_ceiling\.{field} must be a non-negative integer",
                    ):
                        evaluate_experiment(spec, self.records)

    def test_malformed_spec_types_are_rejected(self) -> None:
        cases = (
            ("experiment_id", None, "experiment_id, phase, and budget_id"),
            ("phase", 7, "experiment_id, phase, and budget_id"),
            ("budget_id", "", "experiment_id, phase, and budget_id"),
            (
                "required_problem_ids",
                tuple(self.spec["required_problem_ids"]),
                "required_problem_ids",
            ),
            ("cost_model_frozen", 1, "cost_model_frozen must be boolean"),
            ("budget_ceiling", [], "budget_ceiling must be an object"),
            ("familywise_alpha", True, "familywise_alpha must be between 0 and 1"),
            ("familywise_alpha", "0.05", "familywise_alpha must be between 0 and 1"),
            ("familywise_alpha", 0, "familywise_alpha must be between 0 and 1"),
            ("familywise_alpha", 1, "familywise_alpha must be between 0 and 1"),
        )
        for field, value, message in cases:
            with self.subTest(field=field, value=repr(value)):
                spec = copy.deepcopy(self.spec)
                spec[field] = value
                with self.assertRaisesRegex(ValueError, message):
                    evaluate_experiment(spec, self.records)

        duplicate_problem_spec = copy.deepcopy(self.spec)
        duplicate_problem_spec["required_problem_ids"].append(
            duplicate_problem_spec["required_problem_ids"][0]
        )
        with self.assertRaisesRegex(ValueError, "required_problem_ids must be unique"):
            evaluate_experiment(duplicate_problem_spec, self.records)

    def test_complete_result_is_invariant_to_record_input_order(self) -> None:
        spec = self.frozen_spec()
        baseline = evaluate_experiment(spec, self.records)
        for shift in range(len(self.records)):
            rotated = self.records[shift:] + self.records[:shift]
            for reversed_order, records in (
                (False, rotated),
                (True, list(reversed(rotated))),
            ):
                with self.subTest(kind="rotation", shift=shift, reversed=reversed_order):
                    self.assertEqual(baseline, evaluate_experiment(spec, records))

        rng = random.Random(0)
        for index in range(100):
            records = copy.deepcopy(self.records)
            rng.shuffle(records)
            with self.subTest(kind="shuffle", index=index):
                self.assertEqual(baseline, evaluate_experiment(spec, records))


if __name__ == "__main__":
    unittest.main()
