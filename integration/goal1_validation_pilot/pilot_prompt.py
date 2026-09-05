"""Prospective NON-CREDIT prompt composition; frozen protocol bytes stay intact."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256

from supernova_goal1.confirmatory_io import (
    FINAL_PREFIX,
    NO_ANSWER,
    PRODUCT_PREFIX,
    product_declaration_name,
    render_baseline_prompt,
    render_product_prompt,
)
from supernova_goal1.production_verifier import FrozenLeanProblemSource

PILOT_PRODUCT_PROMPT_VERSION = "supernova.non-credit-product-prompt.v1"
PILOT_BASELINE_PROMPT_VERSION = "supernova.non-credit-baseline-prompt.v1"


def _digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


@dataclass(frozen=True)
class PilotProductPrompt:
    frozen_prompt_utf8: bytes
    output_contract_utf8: bytes
    frozen_template_sha256: str
    product_contract_canonical_sha256: str
    classifier_contract_sha256: str
    source_sha256: str
    attempt: int
    required_product_declaration_name: str
    ordered_admitted_product_sha256: tuple[str, ...]

    @property
    def prompt_utf8(self) -> bytes:
        return self.frozen_prompt_utf8 + b"\n" + self.output_contract_utf8

    def provenance(self) -> dict[str, object]:
        """JSON-safe byte counts and digests of the exact prospective rendering."""
        return {
            "prompt_version": PILOT_PRODUCT_PROMPT_VERSION,
            "classification": "NON_CREDIT_PILOT_PROMPT_ONLY",
            "scientific_credit": "NONE",
            "frozen_template_sha256": self.frozen_template_sha256,
            "product_contract_canonical_sha256": self.product_contract_canonical_sha256,
            "classifier_contract_sha256": self.classifier_contract_sha256,
            "frozen_prompt_utf8_bytes": len(self.frozen_prompt_utf8),
            "frozen_prompt_sha256": _digest(self.frozen_prompt_utf8),
            "output_contract_utf8_bytes": len(self.output_contract_utf8),
            "output_contract_sha256": _digest(self.output_contract_utf8),
            "prompt_utf8_bytes": len(self.prompt_utf8),
            "prompt_sha256": _digest(self.prompt_utf8),
            "source_sha256": self.source_sha256,
            "attempt": self.attempt,
            "required_product_declaration_name": self.required_product_declaration_name,
            "ordered_admitted_product_sha256": list(
                self.ordered_admitted_product_sha256
            ),
        }


def build_pilot_product_prompt(
    contract: Mapping[str, object],
    source: FrozenLeanProblemSource,
    *,
    attempt: int,
    admitted_products: Sequence[bytes],
) -> PilotProductPrompt:
    """Append an explicit contract shared by product_only and verified_chain.

    There is deliberately no arm, verifier-result, or response-adaptation input.
    The supplied products remain the caller's admission decision. Examples are
    outside admitted memory and confer neither admission nor scientific credit.
    """
    products = tuple(admitted_products)
    frozen = render_product_prompt(
        contract, source, attempt=attempt, admitted_products=products
    )
    name = product_declaration_name(source, attempt)
    shared = contract["shared"]
    projection = shared["product_chain_shared_surface"]["projection"]
    product_only = contract["arms"]["product_only"]
    discriminator = projection["response_discriminator"]
    policy = projection["product_declaration_policy"]
    expected_discriminator = {
        "product_prefix_lines": PRODUCT_PREFIX.decode("utf-8").splitlines(),
        "final_answer_prefix_lines": FINAL_PREFIX.decode("utf-8").splitlines(),
        "classification": "EXACT_FIRST_TWO_VISIBLE_LINES",
        "missing_duplicate_or_ambiguous_discriminator": "BLOCKED",
        "no_answer_exact_utf8": NO_ANSWER.decode("utf-8"),
    }
    if (
        discriminator != expected_discriminator
        or product_only["response_discriminator"] != discriminator
    ):
        raise ValueError(
            "pilot discriminator configuration disagrees with classifier constants"
        )
    if product_only["product_declaration_policy"] != policy:
        raise ValueError("pilot product declaration policies disagree")
    configured_name = (
        policy["required_name_template"]
        .replace("{FULL_PROBLEM_SHA256}", source.source_sha256)
        .replace("{ATTEMPT_INDEX_TWO_DIGITS}", f"{attempt:02d}")
    )
    if (
        configured_name != name
        or type(policy["declaration_count"]) is not int
        or policy["declaration_count"] != 1
        or policy["allowed_declaration_kinds"] != ["theorem", "lemma"]
        or policy["unique_name_and_attempt_binding_required"] is not True
    ):
        raise ValueError(
            "pilot product declaration grammar disagrees with classifier name policy"
        )
    safety = shared["response_safety"]
    maximum_bytes = safety["absolute_max_visible_response_utf8_bytes"]
    if (
        safety["encoding"] != "UTF-8"
        or type(maximum_bytes) is not int
        or maximum_bytes < 1
    ):
        raise ValueError("pilot response safety configuration changed")
    if (
        projection["model_visible_prompt_template"]
        != shared["product_chain_prompt_template"]
    ):
        raise ValueError("pilot shared prompt templates disagree")

    output_contract = (
        (
            f"BEGIN_NON_CREDIT_PILOT_OUTPUT_CONTRACT\n"
            f"PILOT_PROMPT_VERSION={PILOT_PRODUCT_PROMPT_VERSION}\n"
            "This prospective NON-CREDIT pilot supplement makes the response syntax explicit.\n"
            "Choose exactly one response: PRODUCT_CANDIDATE, FINAL_ANSWER, or NO_ANSWER.\n"
            f"Use UTF-8, LF line endings, and at most {maximum_bytes} response bytes.\n"
            "Start at byte zero with the exact two discriminator lines shown for your choice.\n"
            "Return only the response bytes; example labels, Markdown fences, and prose are not response bytes.\n"
            "PRODUCT_CANDIDATE: after its discriminator, write exactly one theorem or lemma,\n"
            f"named exactly {name}, with its statement and proof.\n"
            "The following minimal example illustrates declaration syntax; supply a useful product for the frozen theorem.\n"
            "BEGIN_PRODUCT_CANDIDATE_EXAMPLE\n"
        ).encode()
        + PRODUCT_PREFIX
        + (
            f"lemma {name} : True := by\n  trivial\n"
            "END_PRODUCT_CANDIDATE_EXAMPLE\n"
            "FINAL_ANSWER: after its discriminator, write only the tactic body for the frozen proof hole.\n"
            "The following example illustrates tactic-body syntax; supply tactics proving the frozen theorem.\n"
            "The body starts directly with an indented tactic, without a theorem declaration or an outer by.\n"
            "Keep the leading spaces on the first tactic and the relative indentation on later lines.\n"
            "BEGIN_FINAL_ANSWER_EXAMPLE\n"
        ).encode()
        + FINAL_PREFIX
        + (
            b"  rfl\n"
            b"END_FINAL_ANSWER_EXAMPLE\n"
            b"NO_ANSWER: return exactly the following two lines, including the final LF, and no other bytes.\n"
            b"BEGIN_NO_ANSWER_EXACT_BYTES\n"
        )
        + NO_ANSWER
        + (
            b"END_NO_ANSWER_EXACT_BYTES\n"
            b"These examples are output-format instructions, not admitted products or theorem evidence.\n"
            b"The frozen declaration and tactic policies above continue to apply.\n"
            b"END_NON_CREDIT_PILOT_OUTPUT_CONTRACT\n"
        )
    )
    classifier_contract = {
        "response_discriminator": expected_discriminator,
        "product_declaration_policy": policy,
        "response_safety": safety,
    }
    return PilotProductPrompt(
        frozen_prompt_utf8=frozen,
        output_contract_utf8=output_contract,
        frozen_template_sha256=_digest(
            shared["product_chain_prompt_template"].encode("utf-8")
        ),
        product_contract_canonical_sha256=_digest(_canonical(contract)),
        classifier_contract_sha256=_digest(_canonical(classifier_contract)),
        source_sha256=source.source_sha256,
        attempt=attempt,
        required_product_declaration_name=name,
        ordered_admitted_product_sha256=tuple(_digest(value) for value in products),
    )


def render_pilot_product_prompt(
    contract: Mapping[str, object],
    source: FrozenLeanProblemSource,
    *,
    attempt: int,
    admitted_products: Sequence[bytes],
) -> bytes:
    return build_pilot_product_prompt(
        contract, source, attempt=attempt, admitted_products=admitted_products
    ).prompt_utf8


def render_pilot_baseline_prompt(
    contract: Mapping[str, object],
    source: FrozenLeanProblemSource,
    *,
    arm: str,
    attempt: int,
) -> bytes:
    """Append representation guidance to the exact frozen baseline rendering."""
    frozen = render_baseline_prompt(contract, source, arm=arm, attempt=attempt)
    supplement = (
        "BEGIN_NON_CREDIT_PILOT_BASELINE_OUTPUT_CONTRACT\n"
        f"PILOT_PROMPT_VERSION={PILOT_BASELINE_PROMPT_VERSION}\n"
        "This prospective NON-CREDIT pilot supplement makes the tactic-body layout explicit.\n"
        "Return only the tactic body inserted immediately after the frozen theorem's ':= by' and LF.\n"
        "Start the first tactic with its leading indentation (normally two spaces).\n"
        "Keep that leading indentation and the relative indentation of every later line.\n"
        "The response bytes are inserted verbatim; a complete theorem declaration is unnecessary.\n"
        "Layout illustration only: replace the angle-bracket placeholder with your own proof tactics.\n"
        "BEGIN_TACTIC_BODY_LAYOUT\n"
        "  <tactics proving the frozen goal>\n"
        "END_TACTIC_BODY_LAYOUT\n"
        "Return the indented tactics alone, without the layout labels, placeholder, Markdown fences, or an outer by.\n"
        "END_NON_CREDIT_PILOT_BASELINE_OUTPUT_CONTRACT\n"
    ).encode()
    return frozen + b"\n" + supplement
