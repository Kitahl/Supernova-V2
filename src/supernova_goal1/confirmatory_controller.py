"""Frozen arm state machines for Goal-1 confirmatory execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any

from .confirmatory_io import (
    ClassifiedResponse,
    ConfirmatoryResponseKind,
    build_verification_subject,
    classify_baseline_response,
    classify_product_response,
    render_multi_fidelity_prompt,
    render_product_prompt,
)
from .production_verifier import (
    FrozenLeanProblemSource,
    ProductionVerification,
    VerificationSubject,
    canonical_sha256,
)
from .verifier import VerifierStatus

ATTEMPTS = tuple(range(16))


class ProductChainArm(StrEnum):
    PRODUCT_ONLY = "product_only"
    VERIFIED_CHAIN = "verified_chain"


@dataclass(frozen=True)
class ProductAttemptRecord:
    attempt: int
    response_kind: ConfirmatoryResponseKind
    response_sha256: str
    syntax_admissible: bool
    verifier_invoked: bool
    verifier_record_sha256: str | None
    verifier_status: VerifierStatus | None
    product_admitted: bool
    final_solved: bool


@dataclass(frozen=True)
class MultiFidelityAttemptRecord:
    attempt: int
    stage_id: str
    fidelity_rank: int
    candidate_id: str
    selected_predecessor_attempt: int | None
    response_utf8: bytes
    response_sha256: str
    self_score: int
    subject: VerificationSubject
    verifier_record_sha256: str | None = None
    verifier_status: VerifierStatus | None = None

    def __post_init__(self) -> None:
        if type(self.response_utf8) is not bytes:
            raise TypeError("response_utf8 must be exact bytes")


def _exact_dict(value: object, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{field} must be one exact object")
    return value


def _subject_matches(
    verification: ProductionVerification,
    subject: VerificationSubject,
) -> None:
    if type(verification) is not ProductionVerification:
        raise TypeError("verification must be exact ProductionVerification")
    binding = verification.binding
    if (
        binding.candidate_source_sha256 != sha256(subject.candidate_source).hexdigest()
        or binding.source_construction_sha256 != subject.source_construction_sha256
        or binding.theorem_statement_sha256 != subject.theorem_statement_sha256
        or binding.theorem_target_set_sha256 != subject.theorem_target_set_sha256
    ):
        raise ValueError("signed verifier evidence differs from the frozen subject")


def product_admission_decision(
    arm: ProductChainArm | str,
    response_kind: ConfirmatoryResponseKind,
    verifier_status: VerifierStatus,
) -> bool:
    """Pure frozen rule; callers still need signed evidence to supply status."""

    parsed_arm = ProductChainArm(arm)
    return response_kind is ConfirmatoryResponseKind.PRODUCT_CANDIDATE and (
        parsed_arm is ProductChainArm.PRODUCT_ONLY
        or verifier_status is VerifierStatus.PASS
    )


def final_solve_decision(
    response_kind: ConfirmatoryResponseKind,
    verifier_status: VerifierStatus,
) -> bool:
    return (
        response_kind is ConfirmatoryResponseKind.FINAL_ANSWER
        and verifier_status is VerifierStatus.PASS
    )


class ProductChainController:
    """One strict 16-node product-only or verified-chain controller."""

    def __init__(
        self,
        *,
        arm: ProductChainArm | str,
        source: FrozenLeanProblemSource,
        product_contract: Mapping[str, object],
    ) -> None:
        try:
            parsed_arm = ProductChainArm(arm)
        except (TypeError, ValueError) as exc:
            raise ValueError("unknown product-chain arm") from exc
        if type(source) is not FrozenLeanProblemSource:
            raise TypeError("source must be exact FrozenLeanProblemSource")
        contract = _exact_dict(product_contract, "product_contract")
        if (
            contract.get("status") != "FROZEN"
            or contract.get("contract_id") != "goal1-confirmatory-product-controls-v1"
        ):
            raise ValueError("product-control authority is not the frozen contract")
        self.arm = parsed_arm
        self.source = source
        self.contract = contract
        self._records: list[ProductAttemptRecord] = []
        self._admitted_products: list[bytes] = []
        self._pending: tuple[int, ClassifiedResponse, VerificationSubject] | None = None

    @property
    def records(self) -> tuple[ProductAttemptRecord, ...]:
        return tuple(self._records)

    @property
    def admitted_products(self) -> tuple[bytes, ...]:
        return tuple(self._admitted_products)

    @property
    def solved_attempts(self) -> tuple[int, ...]:
        return tuple(record.attempt for record in self._records if record.final_solved)

    @property
    def complete(self) -> bool:
        return len(self._records) == len(ATTEMPTS) and self._pending is None

    def render_request(self, attempt: int) -> bytes:
        if self._pending is not None:
            raise RuntimeError("previous verifier slot is not complete")
        if attempt != len(self._records) or attempt not in ATTEMPTS:
            raise ValueError("product attempt is outside the frozen linear order")
        return render_product_prompt(
            self.contract,
            self.source,
            attempt=attempt,
            admitted_products=self.admitted_products,
        )

    def submit_response(
        self,
        attempt: int,
        visible_utf8: bytes,
    ) -> VerificationSubject | None:
        if self._pending is not None:
            raise RuntimeError("a verifier slot is already pending")
        if attempt != len(self._records) or attempt not in ATTEMPTS:
            raise ValueError("product response is outside the frozen linear order")
        response = classify_product_response(visible_utf8, self.source, attempt=attempt)
        syntax_admissible = response.verifier_candidate_utf8 is not None
        if response.kind is ConfirmatoryResponseKind.NO_ANSWER or not syntax_admissible:
            self._records.append(
                ProductAttemptRecord(
                    attempt=attempt,
                    response_kind=response.kind,
                    response_sha256=sha256(response.visible_utf8).hexdigest(),
                    syntax_admissible=syntax_admissible,
                    verifier_invoked=False,
                    verifier_record_sha256=None,
                    verifier_status=None,
                    product_admitted=False,
                    final_solved=False,
                )
            )
            return None
        subject = build_verification_subject(
            self.source,
            response,
            admitted_products=self.admitted_products,
        )
        self._pending = (attempt, response, subject)
        return subject

    def complete_verifier_slot(
        self,
        verification: ProductionVerification,
    ) -> ProductAttemptRecord:
        if self._pending is None:
            raise RuntimeError("no verifier slot is pending")
        attempt, response, subject = self._pending
        _subject_matches(verification, subject)
        status = verification.result.status
        product_admitted = product_admission_decision(
            self.arm,
            response.kind,
            status,
        )
        final_solved = final_solve_decision(response.kind, status)
        if product_admitted:
            self._admitted_products.append(response.visible_utf8)
        record = ProductAttemptRecord(
            attempt=attempt,
            response_kind=response.kind,
            response_sha256=sha256(response.visible_utf8).hexdigest(),
            syntax_admissible=True,
            verifier_invoked=True,
            verifier_record_sha256=verification.record.record_sha256,
            verifier_status=status,
            product_admitted=product_admitted,
            final_solved=final_solved,
        )
        self._records.append(record)
        self._pending = None
        return record


class MultiFidelityController:
    """Successive halving with a hard model-phase/verifier-phase barrier."""

    def __init__(
        self,
        *,
        source: FrozenLeanProblemSource,
        baseline_contract: Mapping[str, object],
        product_contract: Mapping[str, object],
    ) -> None:
        if type(source) is not FrozenLeanProblemSource:
            raise TypeError("source must be exact FrozenLeanProblemSource")
        baselines = _exact_dict(baseline_contract, "baseline_contract")
        products = _exact_dict(product_contract, "product_contract")
        if baselines.get("status") != "FROZEN" or products.get("status") != "FROZEN":
            raise ValueError("multi-fidelity prompt authorities are not frozen")
        multi = _exact_dict(
            _exact_dict(products["arms"], "arms")["multi_fidelity"], "multi_fidelity"
        )
        stages = multi.get("stages")
        graph = multi.get("promotion_graph")
        if type(stages) is not list or type(graph) is not list:
            raise ValueError("multi-fidelity stages or promotion graph changed")
        self.source = source
        self.baseline_contract = baselines
        self.product_contract = products
        self._slots: dict[int, dict[str, object]] = {}
        for stage in stages:
            stage = _exact_dict(stage, "multi-fidelity stage")
            attempt_indices = stage.get("attempt_indices")
            if type(attempt_indices) is not list:
                raise ValueError("multi-fidelity attempt indices changed")
            candidates = stage.get("candidate_slots")
            for index, attempt in enumerate(attempt_indices):
                if type(attempt) is not int or attempt not in ATTEMPTS:
                    raise ValueError("multi-fidelity attempt changed")
                self._slots[attempt] = {
                    "candidate_id": (
                        candidates[index] if type(candidates) is list else None
                    ),
                    "eligible": (),
                    "fidelity_rank": stage["fidelity_rank"],
                    "stage_id": stage["stage_id"],
                    "visible_output_cap_utf8_bytes": stage[
                        "visible_output_cap_utf8_bytes"
                    ],
                }
        for item in graph:
            item = _exact_dict(item, "promotion graph item")
            attempt = item["attempt_index"]
            eligible = item["eligible_predecessor_attempts"]
            if attempt not in self._slots or type(eligible) is not list:
                raise ValueError("multi-fidelity promotion graph changed")
            self._slots[attempt]["eligible"] = tuple(eligible)
        if set(self._slots) != set(ATTEMPTS):
            raise ValueError("multi-fidelity slots are not exactly 0..15")
        self._records: list[MultiFidelityAttemptRecord] = []
        self._frozen = False
        self._verified_attempts: set[int] = set()

    @property
    def records(self) -> tuple[MultiFidelityAttemptRecord, ...]:
        return tuple(self._records)

    @property
    def model_phase_complete(self) -> bool:
        return len(self._records) == len(ATTEMPTS)

    @property
    def verification_phase_complete(self) -> bool:
        return self._frozen and self._verified_attempts == set(ATTEMPTS)

    @property
    def solved_attempts(self) -> tuple[int, ...]:
        return tuple(
            record.attempt
            for record in self._records
            if record.verifier_status is VerifierStatus.PASS
        )

    def _selected_parent(self, attempt: int) -> MultiFidelityAttemptRecord | None:
        eligible = self._slots[attempt]["eligible"]
        if type(eligible) is not tuple or not eligible:
            return None
        available = [self._records[index] for index in eligible]
        return min(
            available,
            key=lambda record: (
                -record.self_score,
                record.candidate_id,
                record.attempt,
            ),
        )

    def render_request(self, attempt: int) -> bytes:
        if self._frozen:
            raise RuntimeError("model phase is already frozen")
        if attempt != len(self._records) or attempt not in ATTEMPTS:
            raise ValueError("multi-fidelity attempt is outside frozen order")
        slot = self._slots[attempt]
        parent = self._selected_parent(attempt)
        candidate_id = slot["candidate_id"] if parent is None else parent.candidate_id
        if type(candidate_id) is not str:
            raise ValueError("multi-fidelity candidate identity is not bound")
        return render_multi_fidelity_prompt(
            self.baseline_contract,
            self.product_contract,
            self.source,
            attempt=attempt,
            stage_id=str(slot["stage_id"]),
            fidelity_rank=int(slot["fidelity_rank"]),
            candidate_id=candidate_id,
            visible_output_cap_utf8_bytes=int(slot["visible_output_cap_utf8_bytes"]),
        )

    def submit_response(self, attempt: int, visible_utf8: bytes) -> None:
        if self._frozen:
            raise RuntimeError("model phase is already frozen")
        if attempt != len(self._records) or attempt not in ATTEMPTS:
            raise ValueError("multi-fidelity response is outside frozen order")
        slot = self._slots[attempt]
        parent = self._selected_parent(attempt)
        candidate_id = slot["candidate_id"] if parent is None else parent.candidate_id
        response = classify_baseline_response(
            visible_utf8,
            self.source,
            maximum_bytes=int(slot["visible_output_cap_utf8_bytes"]),
            require_self_score=True,
        )
        candidate = (
            b""
            if response.verifier_candidate_utf8 is None
            else response.verifier_candidate_utf8
        )
        theorem_names = (self.source.native_id,)
        subject = VerificationSubject(
            challenge_source=self.source.source,
            candidate_source=candidate,
            theorem_names=theorem_names,
            theorem_statement_sha256=self.source.theorem_statement_sha256,
            theorem_target_set_sha256=canonical_sha256(list(theorem_names)),
            source_construction_sha256=self.source.source_sha256,
        )
        self._records.append(
            MultiFidelityAttemptRecord(
                attempt=attempt,
                stage_id=str(slot["stage_id"]),
                fidelity_rank=int(slot["fidelity_rank"]),
                candidate_id=str(candidate_id),
                selected_predecessor_attempt=(
                    None if parent is None else parent.attempt
                ),
                response_utf8=visible_utf8,
                response_sha256=sha256(visible_utf8).hexdigest(),
                self_score=-1 if response.self_score is None else response.self_score,
                subject=subject,
            )
        )

    def freeze_promotions(self) -> None:
        if not self.model_phase_complete:
            raise RuntimeError(
                "all 16 model calls must complete before promotion freeze"
            )
        self._frozen = True

    def verification_subject(self, attempt: int) -> VerificationSubject:
        if not self._frozen:
            raise RuntimeError("verifier phase cannot start before promotion freeze")
        if attempt not in ATTEMPTS or attempt in self._verified_attempts:
            raise ValueError("multi-fidelity verifier slot is invalid or already used")
        return self._records[attempt].subject

    def complete_verifier_slot(
        self,
        attempt: int,
        verification: ProductionVerification,
    ) -> MultiFidelityAttemptRecord:
        subject = self.verification_subject(attempt)
        _subject_matches(verification, subject)
        current = self._records[attempt]
        updated = MultiFidelityAttemptRecord(
            attempt=current.attempt,
            stage_id=current.stage_id,
            fidelity_rank=current.fidelity_rank,
            candidate_id=current.candidate_id,
            selected_predecessor_attempt=current.selected_predecessor_attempt,
            response_utf8=current.response_utf8,
            response_sha256=current.response_sha256,
            self_score=current.self_score,
            subject=current.subject,
            verifier_record_sha256=verification.record.record_sha256,
            verifier_status=verification.result.status,
        )
        self._records[attempt] = updated
        self._verified_attempts.add(attempt)
        return updated


__all__ = [
    "ATTEMPTS",
    "MultiFidelityAttemptRecord",
    "MultiFidelityController",
    "ProductAttemptRecord",
    "ProductChainArm",
    "ProductChainController",
    "final_solve_decision",
    "product_admission_decision",
]
