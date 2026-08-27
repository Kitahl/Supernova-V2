from __future__ import annotations

import sys
import unittest
from dataclasses import fields
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.arms.ordinary import (
    OrdinaryRequest,
    OrdinaryResult,
    OrdinaryResultStatus,
)
from supernova_goal1.arms.portfolio import (
    PortfolioAttemptStatus,
    PortfolioCandidate,
    PortfolioRequest,
    PortfolioResult,
)


class OrdinaryArmContractTests(unittest.TestCase):
    def request(self) -> OrdinaryRequest:
        return OrdinaryRequest(
            request_id="req-ordinary-1",
            experiment_id="exp-1",
            problem_id="p-1",
            budget_id="budget-1",
            problem_statement="Prove P.",
        )

    def test_ordinary_contract_has_no_intermediate_product_channel(self) -> None:
        self.assertEqual(
            {"request_id", "experiment_id", "problem_id", "budget_id", "problem_statement"},
            {field.name for field in fields(OrdinaryRequest)},
        )
        self.assertEqual(
            {"request_id", "experiment_id", "problem_id", "budget_id", "status", "answer", "error"},
            {field.name for field in fields(OrdinaryResult)},
        )

    def test_answered_result_binds_to_request(self) -> None:
        request = self.request()
        result = OrdinaryResult(
            request_id=request.request_id,
            experiment_id=request.experiment_id,
            problem_id=request.problem_id,
            budget_id=request.budget_id,
            status=OrdinaryResultStatus.ANSWERED,
            answer="proof candidate",
            error=None,
        )
        result.validate_for(request)

    def test_no_answer_and_error_are_typed(self) -> None:
        OrdinaryResult("r", "e", "p", "b", "NO_ANSWER", None, None)
        OrdinaryResult("r", "e", "p", "b", "ERROR", None, "executor failed")
        with self.assertRaisesRegex(ValueError, "ANSWERED"):
            OrdinaryResult("r", "e", "p", "b", "ANSWERED", None, None)
        with self.assertRaisesRegex(ValueError, "cannot carry answer"):
            OrdinaryResult("r", "e", "p", "b", "ERROR", "candidate", "executor failed")

    def test_mapping_rejects_extra_fields(self) -> None:
        raw = {
            "request_id": "r",
            "experiment_id": "e",
            "problem_id": "p",
            "budget_id": "b",
            "problem_statement": "problem",
            "intermediate_products": [],
        }
        with self.assertRaisesRegex(ValueError, "fields must be exactly"):
            OrdinaryRequest.from_mapping(raw)


class PortfolioArmContractTests(unittest.TestCase):
    def request(self) -> PortfolioRequest:
        return PortfolioRequest(
            request_id="req-portfolio-1",
            experiment_id="exp-1",
            problem_id="p-1",
            budget_id="budget-1",
            problem_statement="Prove P.",
            attempt_ids=("attempt-a", "attempt-b", "attempt-c"),
        )

    def test_portfolio_requires_multiple_unique_attempts(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least two"):
            PortfolioRequest("r", "e", "p", "b", "problem", ("a",))
        with self.assertRaisesRegex(ValueError, "unique"):
            PortfolioRequest("r", "e", "p", "b", "problem", ("a", "a"))

    def test_portfolio_selects_one_answer_verbatim(self) -> None:
        request = self.request()
        result = PortfolioResult(
            request_id=request.request_id,
            experiment_id=request.experiment_id,
            problem_id=request.problem_id,
            budget_id=request.budget_id,
            candidates=(
                PortfolioCandidate("attempt-a", PortfolioAttemptStatus.ANSWERED, "answer A", None),
                PortfolioCandidate("attempt-b", PortfolioAttemptStatus.ANSWERED, "answer B", None),
                PortfolioCandidate("attempt-c", PortfolioAttemptStatus.NO_ANSWER, None, None),
            ),
            selected_attempt_id="attempt-b",
        )
        result.validate_for(request)
        self.assertEqual("answer B", result.selected_answer)
        self.assertNotIn("final_answer", {field.name for field in fields(PortfolioResult)})

    def test_selected_attempt_must_be_answered(self) -> None:
        with self.assertRaisesRegex(ValueError, "ANSWERED candidate"):
            PortfolioResult(
                request_id="r",
                experiment_id="e",
                problem_id="p",
                budget_id="b",
                candidates=(
                    PortfolioCandidate("a", "NO_ANSWER", None, None),
                    PortfolioCandidate("b", "ANSWERED", "B", None),
                ),
                selected_attempt_id="a",
            )

    def test_result_must_cover_exact_requested_attempt_set(self) -> None:
        request = self.request()
        result = PortfolioResult(
            request_id=request.request_id,
            experiment_id=request.experiment_id,
            problem_id=request.problem_id,
            budget_id=request.budget_id,
            candidates=(
                PortfolioCandidate("attempt-a", "ANSWERED", "A", None),
                PortfolioCandidate("attempt-b", "ANSWERED", "B", None),
            ),
            selected_attempt_id="attempt-a",
        )
        with self.assertRaisesRegex(ValueError, "exactly the requested attempts"):
            result.validate_for(request)

    def test_mapping_rejects_hidden_cross_attempt_channel(self) -> None:
        raw = {
            "request_id": "r",
            "experiment_id": "e",
            "problem_id": "p",
            "budget_id": "b",
            "problem_statement": "problem",
            "attempt_ids": ["a", "b"],
            "shared_attempt_context": "answer from a",
        }
        with self.assertRaisesRegex(ValueError, "fields must be exactly"):
            PortfolioRequest.from_mapping(raw)


if __name__ == "__main__":
    unittest.main()
