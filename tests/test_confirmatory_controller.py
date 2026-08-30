from __future__ import annotations

import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.confirmatory_controller import (
    MultiFidelityController,
    ProductChainArm,
    ProductChainController,
    final_solve_decision,
    product_admission_decision,
)
from supernova_goal1.confirmatory_io import (
    FINAL_PREFIX,
    PRODUCT_PREFIX,
    ConfirmatoryResponseKind,
    product_declaration_name,
)
from supernova_goal1.production_verifier import FrozenLeanProblemSource
from supernova_goal1.verifier import VerifierStatus


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class ConfirmatoryControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = b"import Mathlib\n\ntheorem demo : 1 + 1 = 2 := by\n"
        cls.source = FrozenLeanProblemSource.from_record(
            {
                "informal_prefix": "",
                "lean_code": source.decode(),
                "lean_code_sha256": sha(source),
                "problem_id": "demo",
                "schema_version": 1,
                "source_id": "fixture",
                "source_record_sha256": "1" * 64,
                "split": "validation",
            },
            expected_split="validation",
        )
        cls.baselines = json.loads(
            (ROOT / "goal1" / "CONFIRMATORY_BASELINES.json").read_text()
        )
        cls.products = json.loads(
            (ROOT / "goal1" / "CONFIRMATORY_PRODUCT_CONTROLS.json").read_text()
        )

    def test_product_admission_and_terminal_solve_tables_are_disjoint(self) -> None:
        statuses = tuple(VerifierStatus)
        for status in statuses:
            self.assertTrue(
                product_admission_decision(
                    ProductChainArm.PRODUCT_ONLY,
                    ConfirmatoryResponseKind.PRODUCT_CANDIDATE,
                    status,
                )
            )
            self.assertEqual(
                status is VerifierStatus.PASS,
                product_admission_decision(
                    ProductChainArm.VERIFIED_CHAIN,
                    ConfirmatoryResponseKind.PRODUCT_CANDIDATE,
                    status,
                ),
            )
            self.assertFalse(
                final_solve_decision(
                    ConfirmatoryResponseKind.PRODUCT_CANDIDATE,
                    status,
                )
            )
            self.assertEqual(
                status is VerifierStatus.PASS,
                final_solve_decision(
                    ConfirmatoryResponseKind.FINAL_ANSWER,
                    status,
                ),
            )

    def test_product_syntax_failure_is_complete_but_never_visible(self) -> None:
        controller = ProductChainController(
            arm=ProductChainArm.PRODUCT_ONLY,
            source=self.source,
            product_contract=self.products,
        )
        controller.render_request(0)
        subject = controller.submit_response(
            0,
            PRODUCT_PREFIX + b"lemma wrong : True := by trivial\n",
        )
        self.assertIsNone(subject)
        self.assertEqual(1, len(controller.records))
        self.assertFalse(controller.records[0].syntax_admissible)
        self.assertFalse(controller.records[0].verifier_invoked)
        self.assertEqual((), controller.admitted_products)
        self.assertNotIn(b"lemma wrong", controller.render_request(1))

    def test_product_or_final_verification_requires_signed_capability(self) -> None:
        controller = ProductChainController(
            arm=ProductChainArm.VERIFIED_CHAIN,
            source=self.source,
            product_contract=self.products,
        )
        name = product_declaration_name(self.source, 0)
        product = PRODUCT_PREFIX + f"lemma {name} : True := by trivial\n".encode()
        subject = controller.submit_response(0, product)
        self.assertIsNotNone(subject)
        with self.assertRaisesRegex(TypeError, "ProductionVerification"):
            controller.complete_verifier_slot(object())  # type: ignore[arg-type]
        with self.assertRaisesRegex(RuntimeError, "previous verifier"):
            controller.render_request(1)

        final_controller = ProductChainController(
            arm=ProductChainArm.VERIFIED_CHAIN,
            source=self.source,
            product_contract=self.products,
        )
        self.assertIsNotNone(
            final_controller.submit_response(0, FINAL_PREFIX + b"norm_num\n")
        )
        self.assertEqual((), final_controller.solved_attempts)

    def test_multi_fidelity_freezes_all_promotions_before_any_verifier(self) -> None:
        controller = MultiFidelityController(
            source=self.source,
            baseline_contract=self.baselines,
            product_contract=self.products,
        )
        scores = {0: 1, 1: 9000, 8: 100, 9: 200}
        secrets: list[bytes] = []
        output_caps: list[int] = []
        for attempt in range(16):
            prompt = controller.render_request(attempt)
            for secret in secrets:
                self.assertNotIn(secret, prompt)
            match = re.search(rb"VISIBLE_OUTPUT_CAP_UTF8_BYTES=([0-9]+)", prompt)
            self.assertIsNotNone(match)
            output_caps.append(int(match.group(1)))  # type: ignore[union-attr]
            if attempt == 8:
                self.assertIn(b"CANDIDATE_ID=C1", prompt)
            secret = f"response-secret-{attempt}".encode()
            secrets.append(secret)
            score = scores.get(attempt, 10 + attempt)
            response = f"-- MULTI_FIDELITY_SELF_SCORE={score:04d}\n".encode()
            response += b"exact (by norm_num)\n-- " + secret + b"\n"
            controller.submit_response(attempt, response)
            with self.assertRaisesRegex(RuntimeError, "promotion freeze"):
                controller.verification_subject(0)

        self.assertEqual(98304, sum(output_caps))
        self.assertTrue(controller.model_phase_complete)
        self.assertEqual(1, controller.records[8].selected_predecessor_attempt)
        self.assertEqual("C1", controller.records[8].candidate_id)
        self.assertFalse(controller.verification_phase_complete)
        controller.freeze_promotions()
        for attempt, record in enumerate(controller.records):
            subject = controller.verification_subject(attempt)
            self.assertEqual(record.response_utf8, subject.candidate_source)

    def test_multi_fidelity_malformed_score_is_minus_one_not_a_retry(self) -> None:
        controller = MultiFidelityController(
            source=self.source,
            baseline_contract=self.baselines,
            product_contract=self.products,
        )
        controller.render_request(0)
        controller.submit_response(0, b"norm_num\n")
        self.assertEqual(-1, controller.records[0].self_score)
        self.assertEqual(1, len(controller.records))


if __name__ == "__main__":
    unittest.main()
