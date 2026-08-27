from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ChainState(StrEnum):
    READY = "READY"
    AWAITING_VERIFICATION = "AWAITING_VERIFICATION"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    COMPLETE = "COMPLETE"


class VerificationOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class InvalidTransitionError(RuntimeError):
    """Raised when an operation is not legal in the current chain state."""


class UnverifiedProductError(InvalidTransitionError):
    """Raised when code tries to consume a product that is not verified."""


class InvalidParentError(ValueError):
    """Raised when a new product is not chained from the last consumed product."""


@dataclass(frozen=True, slots=True)
class ProductRef:
    product_id: str
    step_index: int
    state: ChainState


@dataclass(frozen=True, slots=True)
class VerifiedProduct:
    product_id: str
    step_index: int
    value: Any
    producer_id: str
    verifier_id: str
    evidence_id: str
    parent_product_id: str | None


@dataclass(frozen=True, slots=True)
class StepRecord:
    product_id: str
    step_index: int
    outcome: VerificationOutcome
    producer_id: str
    verifier_id: str
    evidence_id: str
    parent_product_id: str | None
    consumed: bool
    final: bool


@dataclass(slots=True)
class _PendingProduct:
    product_id: str
    step_index: int
    value: Any
    producer_id: str
    parent_product_id: str | None


class VerifiedChain:
    """Runtime typestate for a within-problem verified product chain.

    The chain never exposes an intermediate value through its consumption API until a
    distinct verifier has recorded PASS for the current product. A following step must
    cite the exact ``VerifiedProduct`` object returned by ``consume_verified`` as its
    parent, preserving a concrete verified chain instead of a bag of unrelated products.
    """

    def __init__(self, problem_id: str) -> None:
        if not isinstance(problem_id, str) or not problem_id:
            raise ValueError("problem_id must be a non-empty string")
        self._problem_id = problem_id
        self._state = ChainState.READY
        self._pending: _PendingProduct | None = None
        self._verified: VerifiedProduct | None = None
        self._last_consumed: VerifiedProduct | None = None
        self._used_product_ids: set[str] = set()
        self._history: list[StepRecord] = []

    @property
    def problem_id(self) -> str:
        return self._problem_id

    @property
    def state(self) -> ChainState:
        return self._state

    @property
    def history(self) -> tuple[StepRecord, ...]:
        return tuple(self._history)

    @property
    def current(self) -> ProductRef | None:
        if self._pending is not None:
            return ProductRef(
                product_id=self._pending.product_id,
                step_index=self._pending.step_index,
                state=self._state,
            )
        if self._verified is not None:
            return ProductRef(
                product_id=self._verified.product_id,
                step_index=self._verified.step_index,
                state=self._state,
            )
        return None

    def propose(
        self,
        product_id: str,
        value: Any,
        *,
        producer_id: str,
        parent: VerifiedProduct | None = None,
    ) -> ProductRef:
        if self._state is not ChainState.READY:
            raise InvalidTransitionError(
                f"cannot propose while chain state is {self._state.value}"
            )
        if not isinstance(product_id, str) or not product_id:
            raise ValueError("product_id must be a non-empty string")
        if product_id in self._used_product_ids:
            raise ValueError(f"product_id already used in this chain: {product_id}")
        if not isinstance(producer_id, str) or not producer_id:
            raise ValueError("producer_id must be a non-empty string")

        if self._last_consumed is None:
            if parent is not None:
                raise InvalidParentError("the first product must not declare a parent")
            parent_product_id = None
            step_index = 0
        else:
            if parent is not self._last_consumed:
                raise InvalidParentError(
                    "next product must use the exact last consumed verified product"
                )
            parent_product_id = parent.product_id
            step_index = parent.step_index + 1

        self._pending = _PendingProduct(
            product_id=product_id,
            step_index=step_index,
            value=value,
            producer_id=producer_id,
            parent_product_id=parent_product_id,
        )
        self._verified = None
        self._used_product_ids.add(product_id)
        self._state = ChainState.AWAITING_VERIFICATION
        return ProductRef(product_id, step_index, self._state)

    def record_verification(
        self,
        product_id: str,
        *,
        outcome: VerificationOutcome | str,
        verifier_id: str,
        evidence_id: str,
    ) -> ProductRef:
        if self._state is not ChainState.AWAITING_VERIFICATION or self._pending is None:
            raise InvalidTransitionError(
                f"cannot verify while chain state is {self._state.value}"
            )
        if product_id != self._pending.product_id:
            raise ValueError("verification product_id does not match current product")
        if not isinstance(verifier_id, str) or not verifier_id:
            raise ValueError("verifier_id must be a non-empty string")
        if verifier_id == self._pending.producer_id:
            raise ValueError("verifier_id must differ from producer_id")
        if not isinstance(evidence_id, str) or not evidence_id:
            raise ValueError("evidence_id must be a non-empty string")
        try:
            normalized = VerificationOutcome(outcome)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown verification outcome: {outcome!r}") from exc

        pending = self._pending
        if normalized is VerificationOutcome.PASS:
            self._verified = VerifiedProduct(
                product_id=pending.product_id,
                step_index=pending.step_index,
                value=pending.value,
                producer_id=pending.producer_id,
                verifier_id=verifier_id,
                evidence_id=evidence_id,
                parent_product_id=pending.parent_product_id,
            )
            self._pending = None
            self._state = ChainState.VERIFIED
        else:
            self._history.append(
                StepRecord(
                    product_id=pending.product_id,
                    step_index=pending.step_index,
                    outcome=normalized,
                    producer_id=pending.producer_id,
                    verifier_id=verifier_id,
                    evidence_id=evidence_id,
                    parent_product_id=pending.parent_product_id,
                    consumed=False,
                    final=False,
                )
            )
            self._state = ChainState.REJECTED

        return ProductRef(product_id, pending.step_index, self._state)

    def consume_verified(self, product_id: str) -> VerifiedProduct:
        if self._state is ChainState.AWAITING_VERIFICATION:
            raise UnverifiedProductError("current product has not been verified")
        if self._state is ChainState.REJECTED:
            raise UnverifiedProductError("rejected products cannot be consumed")
        if self._state is not ChainState.VERIFIED or self._verified is None:
            raise InvalidTransitionError(
                f"cannot consume while chain state is {self._state.value}"
            )
        if product_id != self._verified.product_id:
            raise ValueError("consume product_id does not match current product")

        verified = self._verified
        self._history.append(
            StepRecord(
                product_id=verified.product_id,
                step_index=verified.step_index,
                outcome=VerificationOutcome.PASS,
                producer_id=verified.producer_id,
                verifier_id=verified.verifier_id,
                evidence_id=verified.evidence_id,
                parent_product_id=verified.parent_product_id,
                consumed=True,
                final=False,
            )
        )
        self._last_consumed = verified
        self._verified = None
        self._state = ChainState.READY
        return verified

    def discard_rejected(self, product_id: str) -> None:
        if self._state is not ChainState.REJECTED or self._pending is None:
            raise InvalidTransitionError(
                f"cannot discard while chain state is {self._state.value}"
            )
        if product_id != self._pending.product_id:
            raise ValueError("discard product_id does not match current product")
        self._pending = None
        self._state = ChainState.READY

    def finalize(self, product_id: str) -> VerifiedProduct:
        if self._state is ChainState.AWAITING_VERIFICATION:
            raise UnverifiedProductError("unverified products cannot be final outputs")
        if self._state is ChainState.REJECTED:
            raise UnverifiedProductError("rejected products cannot be final outputs")
        if self._state is not ChainState.VERIFIED or self._verified is None:
            raise InvalidTransitionError(
                f"cannot finalize while chain state is {self._state.value}"
            )
        if product_id != self._verified.product_id:
            raise ValueError("finalize product_id does not match current product")

        verified = self._verified
        self._history.append(
            StepRecord(
                product_id=verified.product_id,
                step_index=verified.step_index,
                outcome=VerificationOutcome.PASS,
                producer_id=verified.producer_id,
                verifier_id=verified.verifier_id,
                evidence_id=verified.evidence_id,
                parent_product_id=verified.parent_product_id,
                consumed=True,
                final=True,
            )
        )
        self._last_consumed = verified
        self._verified = None
        self._state = ChainState.COMPLETE
        return verified
