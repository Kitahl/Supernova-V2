from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.confirmatory_io import (
    FINAL_PREFIX,
    NO_ANSWER,
    PRODUCT_PREFIX,
    ConfirmatoryResponseKind,
    build_verification_subject,
    classify_baseline_response,
    classify_product_response,
    product_declaration_name,
    render_baseline_prompt,
    render_multi_fidelity_prompt,
    render_product_prompt,
)
from supernova_goal1.production_verifier import FrozenLeanProblemSource


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class ConfirmatoryIOTests(unittest.TestCase):
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

    def test_baseline_renderer_binds_exact_source_attempt_and_strategy(self) -> None:
        prompt = render_baseline_prompt(
            self.baselines,
            self.source,
            arm="portfolio",
            attempt=2,
        )
        self.assertIn(b"FROZEN_STRATEGY=linarith", prompt)
        self.assertIn(b"ATTEMPT=2", prompt)
        self.assertIn(self.source.source, prompt)
        self.assertNotIn(b"{SOURCE_SHA256}", prompt)

    def test_product_renderer_binds_name_and_only_ordered_admitted_bytes(self) -> None:
        first = PRODUCT_PREFIX + b"lemma prior : True := by trivial\n"
        prompt = render_product_prompt(
            self.products,
            self.source,
            attempt=3,
            admitted_products=(first,),
        )
        self.assertIn(product_declaration_name(self.source, 3).encode(), prompt)
        self.assertIn(first, prompt)
        self.assertNotIn(b"{ORDERED_ADMITTED_PRODUCT_RESPONSE_BYTES}", prompt)

    def test_exact_discriminators_and_product_name_are_enforced(self) -> None:
        name = product_declaration_name(self.source, 0)
        product = PRODUCT_PREFIX + f"lemma {name} : True := by trivial\n".encode()
        classified = classify_product_response(product, self.source, attempt=0)
        self.assertIs(classified.kind, ConfirmatoryResponseKind.PRODUCT_CANDIDATE)
        self.assertEqual(name, classified.theorem_name)
        final = classify_product_response(
            FINAL_PREFIX + b"norm_num\n", self.source, attempt=1
        )
        self.assertIs(final.kind, ConfirmatoryResponseKind.FINAL_ANSWER)
        empty = classify_product_response(NO_ANSWER, self.source, attempt=2)
        self.assertIs(empty.kind, ConfirmatoryResponseKind.NO_ANSWER)
        malformed = classify_product_response(
            PRODUCT_PREFIX + b"lemma wrong : True := by trivial\n",
            self.source,
            attempt=0,
        )
        self.assertIs(malformed.kind, ConfirmatoryResponseKind.PRODUCT_CANDIDATE)
        self.assertIsNone(malformed.verifier_candidate_utf8)

    def test_product_subject_excludes_target_and_final_subject_preserves_it(
        self,
    ) -> None:
        name = product_declaration_name(self.source, 0)
        product = classify_product_response(
            PRODUCT_PREFIX + f"lemma {name} : True := by trivial\n".encode(),
            self.source,
            attempt=0,
        )
        product_subject = build_verification_subject(self.source, product)
        self.assertNotIn(b"theorem demo", product_subject.challenge_source)
        self.assertEqual((name,), product_subject.theorem_names)
        final = classify_product_response(
            FINAL_PREFIX + b"norm_num\n", self.source, attempt=1
        )
        final_subject = build_verification_subject(
            self.source,
            final,
            admitted_products=(product.visible_utf8,),
        )
        self.assertIn(product.visible_utf8, final_subject.challenge_source)
        self.assertTrue(final_subject.challenge_source.endswith(b":= by\n"))
        self.assertEqual(("demo",), final_subject.theorem_names)

    def test_multi_fidelity_score_is_exact_and_verifier_candidate_is_unchanged(
        self,
    ) -> None:
        response = b"-- MULTI_FIDELITY_SELF_SCORE=0042\nnorm_num\n"
        classified = classify_baseline_response(
            response,
            self.source,
            maximum_bytes=2048,
            require_self_score=True,
        )
        self.assertEqual(42, classified.self_score)
        self.assertEqual(response, classified.verifier_candidate_utf8)
        malformed = classify_baseline_response(
            b"norm_num\n",
            self.source,
            maximum_bytes=2048,
            require_self_score=True,
        )
        self.assertEqual(-1, malformed.self_score)

    def test_multi_fidelity_renderer_has_no_predecessor_response_channel(self) -> None:
        prompt = render_multi_fidelity_prompt(
            self.baselines,
            self.products,
            self.source,
            attempt=8,
            stage_id="S1",
            fidelity_rank=1,
            candidate_id="C7",
            visible_output_cap_utf8_bytes=4096,
        )
        self.assertIn(b"CANDIDATE_ID=C7", prompt)
        self.assertIn(b"VISIBLE_OUTPUT_CAP_UTF8_BYTES=4096", prompt)
        self.assertNotIn(b"PREDECESSOR_RESPONSE", prompt)


if __name__ == "__main__":
    unittest.main()
