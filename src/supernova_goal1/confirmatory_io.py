"""Byte-exact model I/O and verifier subjects for the frozen Goal-1 protocol."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from .production_verifier import (
    FrozenLeanProblemSource,
    VerificationSubject,
    canonical_sha256,
)

PRODUCT_PREFIX = (
    b"-- supernova-kind: PRODUCT_CANDIDATE\n"
    b"-- supernova-schema: supernova.product-candidate-emission.v1\n"
)
FINAL_PREFIX = (
    b"-- supernova-kind: FINAL_ANSWER\n"
    b"-- supernova-schema: supernova.final-answer-emission.v1\n"
)
NO_ANSWER = (
    b"-- supernova-kind: NO_ANSWER\n"
    b"-- supernova-schema: supernova.no-answer-emission.v1\n"
)
_FORBIDDEN_PRODUCT_TOKENS = re.compile(
    rb"(?m)^\s*(?:import|namespace|end|section|variable|notation|macro|syntax|"
    rb"attribute|set_option|axiom|opaque|unsafe)\b|\b(?:sorry|admit)\b"
)
_FORBIDDEN_FINAL_TOKENS = re.compile(
    rb"(?m)^\s*(?:import|theorem|lemma|axiom|namespace|section|variable|"
    rb"notation|macro|syntax|attribute|set_option|opaque|unsafe)\b|"
    rb"\b(?:sorry|admit)\b"
)
_SELF_SCORE = re.compile(rb"^-- MULTI_FIDELITY_SELF_SCORE=([0-9]{4})$")


def _replace_once(value: str, placeholder: str, replacement: str) -> str:
    if value.count(placeholder) != 1:
        raise ValueError(f"prompt placeholder {placeholder} is not unique")
    return value.replace(placeholder, replacement)


def _utf8(value: bytes, field: str) -> bytes:
    if type(value) is not bytes:
        raise TypeError(f"{field} must be exact bytes")
    try:
        value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{field} must be UTF-8") from exc
    return value


def _source_prefix(source: FrozenLeanProblemSource) -> bytes:
    marker = b"theorem " + source.native_id.encode() + b" "
    starts = [
        index
        for index in range(len(source.source))
        if source.source.startswith(marker, index)
        and (index == 0 or source.source[index - 1 : index] == b"\n")
    ]
    if len(starts) != 1:
        raise ValueError("frozen target declaration is not unique")
    return source.source[: starts[0]]


def product_declaration_name(source: FrozenLeanProblemSource, attempt: int) -> str:
    if type(attempt) is not int or not 0 <= attempt <= 15:
        raise ValueError("attempt must be an integer in 0..15")
    return f"SupernovaProduct.P_{source.source_sha256}.a{attempt:02d}"


class ConfirmatoryResponseKind(StrEnum):
    PRODUCT_CANDIDATE = "PRODUCT_CANDIDATE"
    FINAL_ANSWER = "FINAL_ANSWER"
    NO_ANSWER = "NO_ANSWER"


@dataclass(frozen=True)
class ClassifiedResponse:
    kind: ConfirmatoryResponseKind
    visible_utf8: bytes
    verifier_candidate_utf8: bytes | None
    theorem_name: str | None
    self_score: int | None

    def __post_init__(self) -> None:
        _utf8(self.visible_utf8, "visible_utf8")
        if self.verifier_candidate_utf8 is not None:
            _utf8(self.verifier_candidate_utf8, "verifier_candidate_utf8")
        if self.kind is ConfirmatoryResponseKind.NO_ANSWER and (
            self.visible_utf8 not in {b"", NO_ANSWER}
            or self.theorem_name is not None
            or self.self_score is not None
            or self.verifier_candidate_utf8 not in {None, b""}
        ):
            raise ValueError("NO_ANSWER response fields changed")


def render_baseline_prompt(
    contract: Mapping[str, object],
    source: FrozenLeanProblemSource,
    *,
    arm: str,
    attempt: int,
    stage_id: str | None = None,
    fidelity_rank: int | None = None,
    candidate_id: str | None = None,
    visible_output_cap_utf8_bytes: int | None = None,
) -> bytes:
    """Render ordinary, portfolio, or multi-fidelity bytes by single substitution."""

    common = contract["common"]
    if type(common) is not dict:
        raise ValueError("baseline common contract changed")
    template = common["prompt_rendering"]["common_template"]
    if type(template) is not str:
        raise ValueError("baseline prompt template changed")
    arms = contract["arms"]
    if type(arms) is not dict or arm not in arms or type(arms[arm]) is not dict:
        raise ValueError("baseline arm is not frozen")
    arm_contract = arms[arm]
    delta = arm_contract["prompt_delta"]
    if type(delta) is not str:
        raise ValueError("baseline prompt delta changed")
    strategy = ""
    if arm == "portfolio":
        schedule = arm_contract["strategy_schedule"]
        if type(schedule) is not list or len(schedule) != 16:
            raise ValueError("portfolio strategy schedule changed")
        strategy = schedule[attempt]
    replacements = {
        "{SOURCE_SHA256}": source.source_sha256,
        "{THEOREM_NAME}": source.native_id,
        "{SOURCE_WITH_PROOF_HOLE}": source.source.decode(),
        "{ATTEMPT_INDEX}": str(attempt),
        "{STRATEGY_ID}": strategy,
        "{STAGE_ID}": "" if stage_id is None else stage_id,
        "{FIDELITY_RANK}": "" if fidelity_rank is None else str(fidelity_rank),
        "{CANDIDATE_ID}": "" if candidate_id is None else candidate_id,
        "{VISIBLE_OUTPUT_CAP_UTF8_BYTES}": (
            ""
            if visible_output_cap_utf8_bytes is None
            else str(visible_output_cap_utf8_bytes)
        ),
    }
    rendered = template + "\n" + delta
    for placeholder, replacement in replacements.items():
        count = rendered.count(placeholder)
        if count:
            if count != 1:
                raise ValueError(f"prompt placeholder {placeholder} is not unique")
            rendered = rendered.replace(placeholder, replacement)
    unresolved = [
        placeholder
        for placeholder in replacements
        if placeholder in rendered and placeholder != "{PROOF_HOLE}"
    ]
    if unresolved:
        raise ValueError("baseline prompt contains unresolved frozen placeholder")
    return rendered.encode()


def render_product_prompt(
    contract: Mapping[str, object],
    source: FrozenLeanProblemSource,
    *,
    attempt: int,
    admitted_products: Sequence[bytes],
) -> bytes:
    shared = contract["shared"]
    if type(shared) is not dict:
        raise ValueError("product shared contract changed")
    rendered = shared["product_chain_prompt_template"]
    if type(rendered) is not str:
        raise ValueError("product prompt template changed")
    products = tuple(_utf8(value, "admitted product") for value in admitted_products)
    replacements = {
        "{ATTEMPT_INDEX}": str(attempt),
        "{PRODUCT_DECLARATION_NAME}": product_declaration_name(source, attempt),
        "{SOURCE_SHA256}": source.source_sha256,
        "{THEOREM_NAME}": source.native_id,
        "{SOURCE_WITH_PROOF_HOLE}": source.source.decode(),
        "{ORDERED_ADMITTED_PRODUCT_RESPONSE_BYTES}": b"".join(products).decode(),
    }
    for placeholder, replacement in replacements.items():
        rendered = _replace_once(rendered, placeholder, replacement)
    return rendered.encode()


def render_multi_fidelity_prompt(
    baseline_contract: Mapping[str, object],
    product_contract: Mapping[str, object],
    source: FrozenLeanProblemSource,
    *,
    attempt: int,
    stage_id: str,
    fidelity_rank: int,
    candidate_id: str,
    visible_output_cap_utf8_bytes: int,
) -> bytes:
    """Render one frozen multi-fidelity request without predecessor bytes."""

    common = baseline_contract["common"]
    arms = product_contract["arms"]
    if type(common) is not dict or type(arms) is not dict:
        raise ValueError("multi-fidelity prompt authorities changed")
    multi = arms.get("multi_fidelity")
    if type(multi) is not dict:
        raise ValueError("multi-fidelity arm is not frozen")
    template = common["prompt_rendering"]["common_template"]
    delta = multi["prompt_delta"]
    if type(template) is not str or type(delta) is not str:
        raise ValueError("multi-fidelity prompt templates changed")
    rendered = template + "\n" + delta
    replacements = {
        "{SOURCE_SHA256}": source.source_sha256,
        "{THEOREM_NAME}": source.native_id,
        "{SOURCE_WITH_PROOF_HOLE}": source.source.decode(),
        "{ATTEMPT_INDEX}": str(attempt),
        "{STAGE_ID}": stage_id,
        "{FIDELITY_RANK}": str(fidelity_rank),
        "{CANDIDATE_ID}": candidate_id,
        "{VISIBLE_OUTPUT_CAP_UTF8_BYTES}": str(visible_output_cap_utf8_bytes),
    }
    for placeholder, replacement in replacements.items():
        rendered = _replace_once(rendered, placeholder, replacement)
    return rendered.encode()


def classify_product_response(
    visible_utf8: bytes,
    source: FrozenLeanProblemSource,
    *,
    attempt: int,
    maximum_bytes: int = 32768,
) -> ClassifiedResponse:
    response = _utf8(visible_utf8, "visible response")
    if not response or len(response) > maximum_bytes:
        raise ValueError("product response is empty or exceeds the frozen byte cap")
    if response == NO_ANSWER:
        return ClassifiedResponse(
            ConfirmatoryResponseKind.NO_ANSWER,
            response,
            None,
            None,
            None,
        )
    if response.startswith(PRODUCT_PREFIX):
        body = response[len(PRODUCT_PREFIX) :]
        name = product_declaration_name(source, attempt)
        declaration = re.compile(
            rb"^(?:theorem|lemma) " + re.escape(name.encode()) + rb"\b"
        )
        if not declaration.match(body) or _FORBIDDEN_PRODUCT_TOKENS.search(body):
            return ClassifiedResponse(
                ConfirmatoryResponseKind.PRODUCT_CANDIDATE,
                response,
                None,
                name,
                None,
            )
        return ClassifiedResponse(
            ConfirmatoryResponseKind.PRODUCT_CANDIDATE,
            response,
            response,
            name,
            None,
        )
    if response.startswith(FINAL_PREFIX):
        body = response[len(FINAL_PREFIX) :]
        if not body or _FORBIDDEN_FINAL_TOKENS.search(body):
            raise ValueError("final answer violates the frozen tactic-body policy")
        return ClassifiedResponse(
            ConfirmatoryResponseKind.FINAL_ANSWER,
            response,
            response,
            source.native_id,
            None,
        )
    raise ValueError("product response lacks one exact frozen discriminator")


def classify_baseline_response(
    visible_utf8: bytes,
    source: FrozenLeanProblemSource,
    *,
    maximum_bytes: int = 32768,
    require_self_score: bool = False,
) -> ClassifiedResponse:
    response = _utf8(visible_utf8, "visible response")
    if not response:
        return ClassifiedResponse(
            ConfirmatoryResponseKind.NO_ANSWER,
            response,
            response if require_self_score else None,
            None,
            None,
        )
    if len(response) > maximum_bytes or _FORBIDDEN_FINAL_TOKENS.search(response):
        raise ValueError("baseline response violates the frozen tactic-body policy")
    score = None
    if require_self_score:
        first_line = response.split(b"\n", 1)[0]
        match = _SELF_SCORE.fullmatch(first_line)
        score = -1 if match is None else int(match.group(1))
    return ClassifiedResponse(
        ConfirmatoryResponseKind.FINAL_ANSWER,
        response,
        response,
        source.native_id,
        score,
    )


def build_verification_subject(
    source: FrozenLeanProblemSource,
    response: ClassifiedResponse,
    *,
    admitted_products: Sequence[bytes] = (),
) -> VerificationSubject:
    if response.verifier_candidate_utf8 is None or response.theorem_name is None:
        raise ValueError("NO_ANSWER has no verifier subject")
    products = tuple(_utf8(value, "admitted product") for value in admitted_products)
    prefix = _source_prefix(source) + b"".join(products)
    if response.kind is ConfirmatoryResponseKind.PRODUCT_CANDIDATE:
        challenge = prefix
        statement_digest = sha256(response.verifier_candidate_utf8).hexdigest()
    elif response.kind is ConfirmatoryResponseKind.FINAL_ANSWER:
        challenge = prefix + source.theorem_statement + b":= by\n"
        statement_digest = source.theorem_statement_sha256
    else:
        raise ValueError("NO_ANSWER has no verifier subject")
    theorem_names = (response.theorem_name,)
    return VerificationSubject(
        challenge_source=challenge,
        candidate_source=response.verifier_candidate_utf8,
        theorem_names=theorem_names,
        theorem_statement_sha256=statement_digest,
        theorem_target_set_sha256=canonical_sha256(list(theorem_names)),
        source_construction_sha256=sha256(challenge).hexdigest(),
    )


__all__ = [
    "FINAL_PREFIX",
    "NO_ANSWER",
    "PRODUCT_PREFIX",
    "ClassifiedResponse",
    "ConfirmatoryResponseKind",
    "VerificationSubject",
    "build_verification_subject",
    "classify_baseline_response",
    "classify_product_response",
    "product_declaration_name",
    "render_baseline_prompt",
    "render_multi_fidelity_prompt",
    "render_product_prompt",
]
