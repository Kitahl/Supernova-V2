from __future__ import annotations

import math
import sys
import unittest
from dataclasses import fields
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.arms.multi_fidelity import (
    FidelityStage,
    MultiFidelityCandidate,
    MultiFidelityRequest,
    MultiFidelityResult,
)
from supernova_goal1.arms.product_only import (
    ProductOnlyProduct,
    ProductOnlyRequest,
    ProductOnlyResult,
    ProductOnlyStatus,
)


class ProductOnlyContractTests(unittest.TestCase):
    def request(self) -> ProductOnlyRequest:
        return ProductOnlyRequest(
            request_id="req-product-1",
            experiment_id="exp-1",
            problem_id="p-1",
            budget_id="budget-1",
            problem_statement="Prove P.",
            max_products=3,
        )

    def test_product_only_is_an_unverified_product_chain(self) -> None:
        request_fields = {field.name for field in fields(ProductOnlyRequest)}
        result_fields = {field.name for field in fields(ProductOnlyResult)}
        product_fields = set(ProductOnlyProduct._fields)
        self.assertEqual(
            {
                "request_id",
                "experiment_id",
                "problem_id",
                "budget_id",
                "problem_statement",
                "max_products",
            },
            request_fields,
        )
        self.assertFalse(
            {"verifier_id", "evidence_id", "verification", "admission"}
            & (request_fields | result_fields | product_fields)
        )

    def test_answered_result_may_finalize_current_unverified_product(self) -> None:
        request = self.request()
        result = ProductOnlyResult(
            request_id=request.request_id,
            experiment_id=request.experiment_id,
            problem_id=request.problem_id,
            budget_id=request.budget_id,
            products=(ProductOnlyProduct("lemma-a", None, "A"),),
            status=ProductOnlyStatus.ANSWERED,
            answer="proof using A",
            answer_parent_product_id="lemma-a",
            error=None,
        )
        result.validate_for(request)

    def test_product_ids_are_runtime_choices_bounded_by_max_products(self) -> None:
        request = self.request()
        result = ProductOnlyResult(
            request.request_id,
            request.experiment_id,
            request.problem_id,
            request.budget_id,
            (
                ProductOnlyProduct("runtime-a", None, "A"),
                ProductOnlyProduct("runtime-b", "runtime-a", "B"),
            ),
            "NO_ANSWER",
            None,
            None,
            None,
        )
        result.validate_for(request)

        too_many = ProductOnlyResult(
            request.request_id,
            request.experiment_id,
            request.problem_id,
            request.budget_id,
            (
                ProductOnlyProduct("a", None, "A"),
                ProductOnlyProduct("b", "a", "B"),
                ProductOnlyProduct("c", "b", "C"),
                ProductOnlyProduct("d", "c", "D"),
            ),
            "NO_ANSWER",
            None,
            None,
            None,
        )
        with self.assertRaisesRegex(ValueError, "max_products"):
            too_many.validate_for(request)

    def test_chain_must_be_contiguous(self) -> None:
        with self.assertRaisesRegex(ValueError, "contiguous"):
            ProductOnlyResult(
                "r",
                "e",
                "p",
                "b",
                (
                    ProductOnlyProduct("a", None, "A"),
                    ProductOnlyProduct("b", None, "B"),
                ),
                "NO_ANSWER",
                None,
                None,
                None,
            )

    def test_product_values_match_verified_chain_json_value_domain(self) -> None:
        source = {"lemma": ["A", {"score": 1.5}], "ok": True, "none": None}
        product = ProductOnlyProduct("a", None, source)
        first = product.value
        self.assertEqual(source, first)
        self.assertEqual(64, len(product.content_sha256))

        source["lemma"][1]["score"] = 9.0
        first["lemma"][0] = "mutated"
        self.assertEqual(
            {"lemma": ["A", {"score": 1.5}], "none": None, "ok": True},
            product.value,
        )

        with self.assertRaisesRegex(ValueError, "NaN or infinity"):
            ProductOnlyProduct("bad", None, math.inf)
        with self.assertRaisesRegex(TypeError, "JSON-compatible"):
            ProductOnlyProduct("bad", None, {1, 2, 3})

    def test_product_snapshot_resists_low_level_field_reassignment(self) -> None:
        product = ProductOnlyProduct("a", None, {"answer": [1, 2]})
        original_value = product.value
        original_digest = product.content_sha256

        mutations = (
            ("product_id", "forged"),
            ("parent_product_id", "forged-parent"),
            ("canonical_json", '{"answer":[999]}'),
            ("content_sha256", "0" * 64),
        )
        for field, replacement in mutations:
            with self.subTest(field=field):
                with self.assertRaises(AttributeError):
                    object.__setattr__(product, field, replacement)

        self.assertEqual("a", product.product_id)
        self.assertIsNone(product.parent_product_id)
        self.assertEqual(original_value, product.value)
        self.assertEqual(original_digest, product.content_sha256)

    def test_mapping_rejects_verifier_feedback_channel(self) -> None:
        raw = {
            "request_id": "r",
            "experiment_id": "e",
            "problem_id": "p",
            "budget_id": "b",
            "products": [],
            "status": "NO_ANSWER",
            "answer": None,
            "answer_parent_product_id": None,
            "error": None,
            "verifier_feedback": "PASS",
        }
        with self.assertRaisesRegex(ValueError, "fields must be exactly"):
            ProductOnlyResult.from_mapping(raw)


class MultiFidelityContractTests(unittest.TestCase):
    def request(self) -> MultiFidelityRequest:
        return MultiFidelityRequest(
            request_id="req-mf-1",
            experiment_id="exp-1",
            problem_id="p-1",
            budget_id="budget-1",
            problem_statement="Prove P.",
            stages=(
                FidelityStage("cheap", "fidelity-low", 0),
                FidelityStage("standard", "fidelity-medium", 1),
                FidelityStage("expensive", "fidelity-high", 2),
            ),
        )

    def test_request_requires_multiple_strictly_increasing_fidelities(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least two"):
            MultiFidelityRequest(
                "r",
                "e",
                "p",
                "b",
                "problem",
                (FidelityStage("only", "low", 0),),
            )
        with self.assertRaisesRegex(ValueError, "strictly increase"):
            MultiFidelityRequest(
                "r",
                "e",
                "p",
                "b",
                "problem",
                (FidelityStage("a", "low", 1), FidelityStage("b", "high", 1)),
            )

    def test_request_rejects_duplicate_fidelity_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "fidelity_ids must be unique"):
            MultiFidelityRequest(
                "r",
                "e",
                "p",
                "b",
                "problem",
                (
                    FidelityStage("a", "same-fidelity", 0),
                    FidelityStage("b", "same-fidelity", 1),
                ),
            )

    def test_progressive_fidelity_selects_one_stage_answer_verbatim(self) -> None:
        request = self.request()
        result = MultiFidelityResult(
            request_id=request.request_id,
            experiment_id=request.experiment_id,
            problem_id=request.problem_id,
            budget_id=request.budget_id,
            candidates=(
                MultiFidelityCandidate("cheap", "NO_ANSWER", None, None),
                MultiFidelityCandidate("standard", "ANSWERED", "candidate B", None),
            ),
            selected_stage_id="standard",
        )
        result.validate_for(request)
        self.assertEqual("candidate B", result.selected_answer)
        self.assertNotIn("final_answer", {field.name for field in fields(MultiFidelityResult)})

    def test_result_may_only_execute_an_ordered_stage_prefix(self) -> None:
        request = self.request()
        skipped = MultiFidelityResult(
            request.request_id,
            request.experiment_id,
            request.problem_id,
            request.budget_id,
            (MultiFidelityCandidate("standard", "ANSWERED", "B", None),),
            "standard",
        )
        with self.assertRaisesRegex(ValueError, "ordered prefix"):
            skipped.validate_for(request)

    def test_selected_stage_must_be_final_answered_attempt(self) -> None:
        with self.assertRaisesRegex(ValueError, "final attempted ANSWERED stage"):
            MultiFidelityResult(
                "r",
                "e",
                "p",
                "b",
                (
                    MultiFidelityCandidate("low", "NO_ANSWER", None, None),
                    MultiFidelityCandidate("high", "ANSWERED", "H", None),
                ),
                "low",
            )

    def test_escalation_stops_after_first_answer(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot continue after an ANSWERED stage"):
            MultiFidelityResult(
                "r",
                "e",
                "p",
                "b",
                (
                    MultiFidelityCandidate("low", "ANSWERED", "L", None),
                    MultiFidelityCandidate("high", "ANSWERED", "H", None),
                ),
                "high",
            )

    def test_answer_cannot_be_followed_by_failed_higher_fidelity_attempt(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot continue after an ANSWERED stage"):
            MultiFidelityResult(
                "r",
                "e",
                "p",
                "b",
                (
                    MultiFidelityCandidate("low", "ANSWERED", "L", None),
                    MultiFidelityCandidate("high", "ERROR", None, "timeout"),
                ),
                None,
            )

    def test_escalated_error_has_no_selection(self) -> None:
        request = self.request()
        result = MultiFidelityResult(
            request.request_id,
            request.experiment_id,
            request.problem_id,
            request.budget_id,
            (
                MultiFidelityCandidate("cheap", "NO_ANSWER", None, None),
                MultiFidelityCandidate("standard", "ERROR", None, "timeout"),
            ),
            None,
        )
        result.validate_for(request)
        self.assertIsNone(result.selected_answer)

    def test_multi_fidelity_has_no_cross_stage_product_or_verifier_channel(self) -> None:
        request_fields = {field.name for field in fields(MultiFidelityRequest)}
        result_fields = {field.name for field in fields(MultiFidelityResult)}
        candidate_fields = {field.name for field in fields(MultiFidelityCandidate)}
        self.assertFalse(
            {
                "parent_product_id",
                "intermediate_products",
                "shared_stage_context",
                "verifier_feedback",
                "verification",
            }
            & (request_fields | result_fields | candidate_fields)
        )

        raw = {
            "request_id": "r",
            "experiment_id": "e",
            "problem_id": "p",
            "budget_id": "b",
            "problem_statement": "problem",
            "stages": [
                {"stage_id": "low", "fidelity_id": "f-low", "fidelity_rank": 0},
                {"stage_id": "high", "fidelity_id": "f-high", "fidelity_rank": 1},
            ],
            "shared_stage_context": "candidate from low",
        }
        with self.assertRaisesRegex(ValueError, "fields must be exactly"):
            MultiFidelityRequest.from_mapping(raw)


if __name__ == "__main__":
    unittest.main()
