from __future__ import annotations

import copy
import hashlib
import inspect
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from integration.goal1_validation_pilot.pilot_prompt import (
    PILOT_BASELINE_PROMPT_VERSION,
    PILOT_PRODUCT_PROMPT_VERSION,
    build_pilot_product_prompt,
    render_pilot_baseline_prompt,
    render_pilot_product_prompt,
)
from supernova_goal1.confirmatory_io import (
    FINAL_PREFIX,
    NO_ANSWER,
    PRODUCT_PREFIX,
    ConfirmatoryResponseKind,
    classify_product_response,
    product_declaration_name,
    render_baseline_prompt,
    render_product_prompt,
)
from supernova_goal1.production_verifier import FrozenLeanProblemSource


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def example(prompt: bytes, label: bytes) -> bytes:
    return prompt.split(b"BEGIN_" + label + b"\n", 1)[1].split(
        b"END_" + label + b"\n", 1
    )[0]


class PilotPromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = b"import Mathlib\n\ntheorem demo : 1 + 1 = 2 := by\n"
        cls.source = FrozenLeanProblemSource.from_record(
            {
                "informal_prefix": "",
                "lean_code": source.decode("utf-8"),
                "lean_code_sha256": digest(source),
                "problem_id": "demo",
                "schema_version": 1,
                "source_id": "fixture",
                "source_record_sha256": "1" * 64,
                "split": "validation",
            },
            expected_split="validation",
        )
        cls.contract = json.loads(
            (ROOT / "goal1/CONFIRMATORY_PRODUCT_CONTROLS.json").read_text(
                encoding="utf-8"
            )
        )
        cls.baselines = json.loads(
            (ROOT / "goal1/CONFIRMATORY_BASELINES.json").read_text(encoding="utf-8")
        )

    def build(self, *, attempt: int = 0, products: tuple[bytes, ...] = ()):
        return build_pilot_product_prompt(
            self.contract, self.source, attempt=attempt, admitted_products=products
        )

    def test_first_empty_memory_prompt_exposes_all_exact_discriminators(self) -> None:
        result = self.build()
        self.assertEqual((), result.ordered_admitted_product_sha256)
        self.assertIn(
            b"BEGIN_ADMITTED_PRODUCTS\n\nEND_ADMITTED_PRODUCTS",
            result.frozen_prompt_utf8,
        )
        for prefix in (PRODUCT_PREFIX, FINAL_PREFIX, NO_ANSWER):
            with self.subTest(prefix=prefix):
                self.assertNotIn(prefix, result.frozen_prompt_utf8)
                self.assertEqual(1, result.output_contract_utf8.count(prefix))
                self.assertIn(prefix, result.prompt_utf8)
        self.assertIn(PILOT_PRODUCT_PROMPT_VERSION.encode(), result.prompt_utf8)
        self.assertIn(b"NON-CREDIT", result.output_contract_utf8)

    def test_examples_are_accepted_by_the_actual_classifier(self) -> None:
        for attempt in (0, 1, 9, 15):
            result = self.build(attempt=attempt)
            for label, kind in (
                (
                    b"PRODUCT_CANDIDATE_EXAMPLE",
                    ConfirmatoryResponseKind.PRODUCT_CANDIDATE,
                ),
                (b"FINAL_ANSWER_EXAMPLE", ConfirmatoryResponseKind.FINAL_ANSWER),
                (b"NO_ANSWER_EXACT_BYTES", ConfirmatoryResponseKind.NO_ANSWER),
            ):
                with self.subTest(attempt=attempt, kind=kind):
                    raw = example(result.output_contract_utf8, label)
                    classified = classify_product_response(
                        raw, self.source, attempt=attempt
                    )
                    self.assertIs(kind, classified.kind)
                    self.assertEqual(raw, classified.visible_utf8)
                    if kind is ConfirmatoryResponseKind.PRODUCT_CANDIDATE:
                        name = product_declaration_name(self.source, attempt)
                        self.assertEqual(name, classified.theorem_name)
                        self.assertEqual(
                            f"lemma {name} : True := by\n  trivial\n".encode(),
                            classified.product_parser_source_utf8,
                        )
                    elif kind is ConfirmatoryResponseKind.NO_ANSWER:
                        self.assertEqual(NO_ANSWER, raw)
                    else:
                        self.assertEqual(FINAL_PREFIX + b"  rfl\n", raw)

    def test_discriminator_corruption_still_fails_existing_classifier(self) -> None:
        final = example(self.build().prompt_utf8, b"FINAL_ANSWER_EXAMPLE")
        corruptions = (
            b"\n" + final,
            b"\xef\xbb\xbf" + final,
            final.replace(b"\n", b"\r\n"),
            final.replace(b"FINAL_ANSWER", b"final_answer"),
            final.replace(b"emission.v1", b"emission.v2"),
            final.replace(b"FINAL_ANSWER\n", b"FINAL_ANSWER \n"),
            final.replace(b"-- supernova-schema:", b"-- schema:"),
            b"  rfl\n",
            b"```lean\n" + final + b"```\n",
        )
        for raw in corruptions:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                classify_product_response(raw, self.source, attempt=0)

    def test_no_answer_and_final_body_boundaries_remain_strict(self) -> None:
        for raw in (
            NO_ANSWER[:-1],
            NO_ANSWER + b"\n",
            NO_ANSWER + b"  rfl\n",
            FINAL_PREFIX,
            FINAL_PREFIX + b"theorem demo : True := by trivial\n",
            FINAL_PREFIX + b"  sorry\n",
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                classify_product_response(raw, self.source, attempt=0)

    def test_composition_preserves_frozen_bytes_and_ordered_products(self) -> None:
        products = (
            PRODUCT_PREFIX + b"lemma earlier : True := by\n  trivial\n",
            PRODUCT_PREFIX + "lemma later : True := by\n  -- λ\n  trivial\n".encode(),
        )
        result = self.build(attempt=2, products=products)
        frozen = render_product_prompt(
            self.contract, self.source, attempt=2, admitted_products=products
        )
        self.assertEqual(frozen, result.frozen_prompt_utf8)
        self.assertEqual(
            frozen + b"\n" + result.output_contract_utf8, result.prompt_utf8
        )
        self.assertIn(
            b"BEGIN_ADMITTED_PRODUCTS\n"
            + b"".join(products)
            + b"\nEND_ADMITTED_PRODUCTS",
            frozen,
        )
        self.assertEqual(
            self.build(attempt=2).output_contract_utf8, result.output_contract_utf8
        )
        self.assertEqual(
            tuple(digest(p) for p in products), result.ordered_admitted_product_sha256
        )

    def test_common_surface_has_no_arm_or_verifier_input(self) -> None:
        self.assertEqual(
            ["contract", "source", "attempt", "admitted_products"],
            list(inspect.signature(build_pilot_product_prompt).parameters),
        )
        prompt = self.build().prompt_utf8
        self.assertIn(b"MODEL_VISIBLE_ARM_LABEL=product_chain", prompt)
        self.assertNotIn(b"product_only", prompt)
        self.assertNotIn(b"verified_chain", prompt)
        self.assertEqual(
            prompt,
            render_pilot_product_prompt(
                self.contract, self.source, attempt=0, admitted_products=()
            ),
        )

    def test_config_drift_is_rejected_even_when_discriminator_copies_agree(
        self,
    ) -> None:
        changed = copy.deepcopy(self.contract)
        projection = changed["shared"]["product_chain_shared_surface"]["projection"]
        for policy in (projection, changed["arms"]["product_only"]):
            policy["response_discriminator"]["product_prefix_lines"][1] += "-drift"
        with self.assertRaisesRegex(ValueError, "classifier constants"):
            build_pilot_product_prompt(
                changed, self.source, attempt=0, admitted_products=()
            )

    def test_policy_and_template_drift_are_rejected(self) -> None:
        for field, value in (
            ("required_name_template", "WrongName"),
            ("declaration_count", 2),
            ("declaration_count", True),
            ("allowed_declaration_kinds", ["axiom"]),
            ("unique_name_and_attempt_binding_required", False),
        ):
            changed = copy.deepcopy(self.contract)
            for policy in (
                changed["shared"]["product_chain_shared_surface"]["projection"],
                changed["arms"]["product_only"],
            ):
                policy["product_declaration_policy"][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(
                ValueError, "declaration grammar"
            ):
                build_pilot_product_prompt(
                    changed, self.source, attempt=0, admitted_products=()
                )
        changed = copy.deepcopy(self.contract)
        changed["shared"]["product_chain_shared_surface"]["projection"][
            "model_visible_prompt_template"
        ] += "changed"
        with self.assertRaisesRegex(ValueError, "templates disagree"):
            build_pilot_product_prompt(
                changed, self.source, attempt=0, admitted_products=()
            )

    def test_invalid_attempt_and_non_utf8_memory_fail_before_prompt(self) -> None:
        for attempt in (-1, 16, True, 1.0):
            with self.subTest(attempt=attempt), self.assertRaises(ValueError):
                self.build(attempt=attempt)
        with self.assertRaises(ValueError):
            self.build(products=(b"\xff",))

    def test_deterministic_provenance_binds_exact_bytes_and_inputs(self) -> None:
        before = copy.deepcopy(self.contract)
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        self.assertEqual(before, self.contract)
        provenance = first.provenance()
        self.assertEqual("NONE", provenance["scientific_credit"])
        self.assertEqual([], provenance["ordered_admitted_product_sha256"])
        self.assertEqual(provenance, json.loads(json.dumps(provenance)))
        for prefix, raw in (
            ("frozen_prompt", first.frozen_prompt_utf8),
            ("output_contract", first.output_contract_utf8),
            ("prompt", first.prompt_utf8),
        ):
            self.assertEqual(len(raw), provenance[prefix + "_utf8_bytes"])
            self.assertEqual(digest(raw), provenance[prefix + "_sha256"])
        self.assertNotEqual(
            provenance["prompt_sha256"],
            self.build(attempt=1).provenance()["prompt_sha256"],
        )
        self.assertEqual(
            provenance["classifier_contract_sha256"],
            self.build(attempt=1).provenance()["classifier_contract_sha256"],
        )

    def test_baseline_supplement_preserves_frozen_rendering_and_tactic_layout(
        self,
    ) -> None:
        for arm in ("ordinary", "portfolio"):
            with self.subTest(arm=arm):
                frozen = render_baseline_prompt(
                    self.baselines, self.source, arm=arm, attempt=0
                )
                prompt = render_pilot_baseline_prompt(
                    self.baselines, self.source, arm=arm, attempt=0
                )
                self.assertTrue(
                    prompt.startswith(
                        frozen + b"\nBEGIN_NON_CREDIT_PILOT_BASELINE_OUTPUT_CONTRACT\n"
                    )
                )
                self.assertIn(PILOT_BASELINE_PROMPT_VERSION.encode(), prompt)
                self.assertEqual(
                    b"  <tactics proving the frozen goal>\n",
                    example(prompt, b"TACTIC_BODY_LAYOUT"),
                )
                self.assertIn(b"inserted verbatim", prompt)
                self.assertIn(b"':= by' and LF", prompt)
                self.assertNotIn(FINAL_PREFIX, prompt)


if __name__ == "__main__":
    unittest.main()
