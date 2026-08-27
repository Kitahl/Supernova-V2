from __future__ import annotations

import hashlib
import json
import math
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple, Sequence

from supernova_goal1.verifier import VerifierResult, VerifierStatus, run_verifier

SUBJECT_PATH_TOKEN = "{subject_path}"


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


class VerificationSubjectMismatchError(ValueError):
    """Raised when verification evidence is not bound to the current subject."""


class VerificationAuthorityError(InvalidTransitionError):
    """Raised when no orchestration-owned deterministic verifier is configured."""


class VerificationExecutionError(InvalidTransitionError):
    """Raised when the deterministic verifier times out or errors."""

    def __init__(self, status: VerifierStatus, evidence_id: str, detail: str | None) -> None:
        super().__init__(f"deterministic verifier returned {status.value}: {detail or 'no detail'}")
        self.status = status
        self.evidence_id = evidence_id


def _validate_product_value(value: Any, path: str = "value") -> None:
    if value is None or isinstance(value, (bool, str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain NaN or infinity")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_product_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} object keys must be strings")
            _validate_product_value(item, f"{path}.{key}")
        return
    raise TypeError(
        f"{path} must be JSON-compatible (null, boolean, string, number, list, or object)"
    )


def _hash_record(record: dict[str, Any]) -> str:
    encoded = json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class CanonicalProductValue(NamedTuple):
    """Tuple-backed snapshot whose verified bytes cannot be reassigned in-process."""

    canonical_json: str
    content_sha256: str

    @classmethod
    def from_value(cls, value: Any) -> "CanonicalProductValue":
        _validate_product_value(value)
        canonical_json = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        try:
            canonical_bytes = canonical_json.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(
                "value must contain only UTF-8-encodable Unicode scalar values"
            ) from exc
        return cls(
            canonical_json=canonical_json,
            content_sha256=hashlib.sha256(canonical_bytes).hexdigest(),
        )

    @property
    def sha256(self) -> str:
        return self.content_sha256

    def to_python(self) -> Any:
        return json.loads(self.canonical_json)


def _verification_subject_sha256(
    *,
    problem_id: str,
    product_id: str,
    step_index: int,
    content_sha256: str,
    producer_id: str,
    parent_product_id: str | None,
    parent_content_sha256: str | None,
    parent_verification_subject_sha256: str | None,
    parent_verification_receipt_sha256: str | None,
) -> str:
    return _hash_record(
        {
            "schema": "supernova-goal1-verification-subject-v3",
            "problem_id": problem_id,
            "product_id": product_id,
            "step_index": step_index,
            "content_sha256": content_sha256,
            "producer_id": producer_id,
            "parent_product_id": parent_product_id,
            "parent_content_sha256": parent_content_sha256,
            "parent_verification_subject_sha256": parent_verification_subject_sha256,
            "parent_verification_receipt_sha256": parent_verification_receipt_sha256,
        }
    )


def _verification_receipt_sha256(
    *,
    subject_sha256: str,
    outcome: VerificationOutcome,
    verifier_id: str,
    evidence_id: str,
) -> str:
    return _hash_record(
        {
            "schema": "supernova-goal1-verification-receipt-v2",
            "subject_sha256": subject_sha256,
            "outcome": outcome.value,
            "verifier_id": verifier_id,
            "evidence_id": evidence_id,
        }
    )


def _normalize_verifier_command(command: Sequence[str] | None) -> tuple[str, ...] | None:
    if command is None:
        return None
    if isinstance(command, (str, bytes)):
        raise TypeError("verifier_command must be a sequence of argument strings")
    normalized = tuple(command)
    if not normalized or not all(isinstance(arg, str) and arg for arg in normalized):
        raise ValueError("verifier_command must contain non-empty argument strings")
    if normalized.count(SUBJECT_PATH_TOKEN) != 1:
        raise ValueError(
            f"verifier_command must contain exactly one {SUBJECT_PATH_TOKEN!r} argument"
        )
    return normalized


def _normalize_verifier_timeout(timeout_seconds: float) -> float:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise TypeError("verifier_timeout_seconds must be a finite positive number")
    timeout = float(timeout_seconds)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("verifier_timeout_seconds must be a finite positive number")
    return timeout


def _verifier_id(command_template: tuple[str, ...], timeout_seconds: float) -> str:
    digest = _hash_record(
        {
            "schema": "supernova-goal1-verifier-binding-v1",
            "command_template": list(command_template),
            "timeout_seconds": timeout_seconds,
        }
    )
    return f"command-sha256:{digest}"


def _verifier_evidence_id(
    *,
    subject_sha256: str,
    command_template: tuple[str, ...],
    timeout_seconds: float,
    result: VerifierResult,
) -> str:
    digest = _hash_record(
        {
            "schema": "supernova-goal1-verifier-evidence-v1",
            "subject_sha256": subject_sha256,
            "command_template": list(command_template),
            "timeout_seconds": timeout_seconds,
            "status": result.status.value,
            "returncode": result.returncode,
            "stdout_sha256": _hash_text(result.stdout),
            "stderr_sha256": _hash_text(result.stderr),
            "error": result.error,
        }
    )
    return f"verifier-evidence-sha256:{digest}"


@dataclass(frozen=True, slots=True)
class ProductRef:
    product_id: str
    step_index: int
    state: ChainState
    content_sha256: str


class VerificationSubject(NamedTuple):
    problem_id: str
    product_id: str
    step_index: int
    content: CanonicalProductValue
    producer_id: str
    parent_product_id: str | None
    parent_content_sha256: str | None
    parent_verification_subject_sha256: str | None
    parent_verification_receipt_sha256: str | None
    subject_digest_sha256: str

    @property
    def value(self) -> Any:
        return self.content.to_python()

    @property
    def canonical_json(self) -> str:
        return self.content.canonical_json

    @property
    def content_sha256(self) -> str:
        return self.content.sha256

    @property
    def subject_sha256(self) -> str:
        return self.subject_digest_sha256


class VerifiedProduct(NamedTuple):
    product_id: str
    step_index: int
    content: CanonicalProductValue
    producer_id: str
    verifier_id: str
    evidence_id: str
    parent_product_id: str | None
    verification_subject_sha256: str
    verification_receipt_sha256: str

    @property
    def value(self) -> Any:
        return self.content.to_python()

    @property
    def content_sha256(self) -> str:
        return self.content.sha256


class StepRecord(NamedTuple):
    product_id: str
    step_index: int
    content_sha256: str
    verification_subject_sha256: str
    verification_receipt_sha256: str
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
    content: CanonicalProductValue
    producer_id: str
    parent_product_id: str | None


class VerifiedChain:
    """Within-problem chain whose PASS transition is owned by a real verifier run.

    The chain may be constructed without a verifier so producers can prepare a pending
    subject, but ``verify_pending`` then fails closed. Trusted orchestration configures
    one command template when the chain is created. The template must contain one
    ``{subject_path}`` argument; the chain materializes the immutable subject there and
    calls the timeout-bounded G1-003 ``run_verifier`` adapter. Callers cannot submit a
    PASS/FAIL declaration, verifier identity, evidence identity, or prebuilt
    ``VerifierResult`` to mint VERIFIED.
    """

    def __init__(
        self,
        problem_id: str,
        *,
        verifier_command: Sequence[str] | None = None,
        verifier_timeout_seconds: float = 60.0,
    ) -> None:
        if not isinstance(problem_id, str) or not problem_id:
            raise ValueError("problem_id must be a non-empty string")
        self._problem_id = problem_id
        self._verifier_command_template = _normalize_verifier_command(verifier_command)
        self._verifier_timeout_seconds = _normalize_verifier_timeout(
            verifier_timeout_seconds
        )
        self._verifier_id = (
            _verifier_id(
                self._verifier_command_template, self._verifier_timeout_seconds
            )
            if self._verifier_command_template is not None
            else None
        )
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
    def verifier_id(self) -> str | None:
        return self._verifier_id

    @property
    def history(self) -> tuple[StepRecord, ...]:
        return tuple(self._history)

    @property
    def current(self) -> ProductRef | None:
        if self._pending is not None:
            return ProductRef(
                self._pending.product_id,
                self._pending.step_index,
                self._state,
                self._pending.content.sha256,
            )
        if self._verified is not None:
            return ProductRef(
                self._verified.product_id,
                self._verified.step_index,
                self._state,
                self._verified.content_sha256,
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
        if self._verifier_id is not None and producer_id == self._verifier_id:
            raise ValueError("producer_id must differ from configured verifier identity")

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

        content = CanonicalProductValue.from_value(value)
        self._pending = _PendingProduct(
            product_id, step_index, content, producer_id, parent_product_id
        )
        self._verified = None
        self._used_product_ids.add(product_id)
        self._state = ChainState.AWAITING_VERIFICATION
        return ProductRef(product_id, step_index, self._state, content.sha256)

    def _make_verification_subject(self, pending: _PendingProduct) -> VerificationSubject:
        has_parent = pending.parent_product_id is not None and self._last_consumed is not None
        parent_content_sha256 = self._last_consumed.content_sha256 if has_parent else None
        parent_subject = (
            self._last_consumed.verification_subject_sha256 if has_parent else None
        )
        parent_receipt = (
            self._last_consumed.verification_receipt_sha256 if has_parent else None
        )
        subject_sha256 = _verification_subject_sha256(
            problem_id=self._problem_id,
            product_id=pending.product_id,
            step_index=pending.step_index,
            content_sha256=pending.content.sha256,
            producer_id=pending.producer_id,
            parent_product_id=pending.parent_product_id,
            parent_content_sha256=parent_content_sha256,
            parent_verification_subject_sha256=parent_subject,
            parent_verification_receipt_sha256=parent_receipt,
        )
        return VerificationSubject(
            self._problem_id,
            pending.product_id,
            pending.step_index,
            pending.content,
            pending.producer_id,
            pending.parent_product_id,
            parent_content_sha256,
            parent_subject,
            parent_receipt,
            subject_sha256,
        )

    def verification_subject(self, product_id: str) -> VerificationSubject:
        if self._state is not ChainState.AWAITING_VERIFICATION or self._pending is None:
            raise InvalidTransitionError(
                f"no verification subject while chain state is {self._state.value}"
            )
        if product_id != self._pending.product_id:
            raise ValueError("verification product_id does not match current product")
        return self._make_verification_subject(self._pending)

    @staticmethod
    def _subject_document(subject: VerificationSubject) -> str:
        return json.dumps(
            {
                "schema": "supernova-goal1-verifier-subject-file-v1",
                "problem_id": subject.problem_id,
                "product_id": subject.product_id,
                "step_index": subject.step_index,
                "canonical_json": subject.canonical_json,
                "content_sha256": subject.content_sha256,
                "producer_id": subject.producer_id,
                "parent_product_id": subject.parent_product_id,
                "parent_content_sha256": subject.parent_content_sha256,
                "parent_verification_subject_sha256": subject.parent_verification_subject_sha256,
                "parent_verification_receipt_sha256": subject.parent_verification_receipt_sha256,
                "subject_sha256": subject.subject_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ) + "\n"

    def _apply_verification(
        self,
        pending: _PendingProduct,
        subject: VerificationSubject,
        *,
        outcome: VerificationOutcome,
        verifier_id: str,
        evidence_id: str,
    ) -> ProductRef:
        if pending is not self._pending:
            raise VerificationSubjectMismatchError("pending product changed during verification")
        current_subject = self._make_verification_subject(pending)
        if current_subject.subject_sha256 != subject.subject_sha256:
            raise VerificationSubjectMismatchError(
                "verification subject digest does not match current chain context"
            )

        receipt_sha256 = _verification_receipt_sha256(
            subject_sha256=subject.subject_sha256,
            outcome=outcome,
            verifier_id=verifier_id,
            evidence_id=evidence_id,
        )
        if outcome is VerificationOutcome.PASS:
            self._verified = VerifiedProduct(
                pending.product_id,
                pending.step_index,
                pending.content,
                pending.producer_id,
                verifier_id,
                evidence_id,
                pending.parent_product_id,
                subject.subject_sha256,
                receipt_sha256,
            )
            self._pending = None
            self._state = ChainState.VERIFIED
        else:
            self._history.append(
                StepRecord(
                    pending.product_id,
                    pending.step_index,
                    pending.content.sha256,
                    subject.subject_sha256,
                    receipt_sha256,
                    outcome,
                    pending.producer_id,
                    verifier_id,
                    evidence_id,
                    pending.parent_product_id,
                    False,
                    False,
                )
            )
            self._state = ChainState.REJECTED
        return ProductRef(pending.product_id, pending.step_index, self._state, pending.content.sha256)

    def verify_pending(self, product_id: str) -> ProductRef:
        """Run the fixed deterministic verifier and apply only its typed result."""

        if self._state is not ChainState.AWAITING_VERIFICATION or self._pending is None:
            raise InvalidTransitionError(
                f"cannot verify while chain state is {self._state.value}"
            )
        if product_id != self._pending.product_id:
            raise ValueError("verification product_id does not match current product")
        if self._verifier_command_template is None or self._verifier_id is None:
            raise VerificationAuthorityError(
                "no deterministic verifier is configured for this chain"
            )

        pending = self._pending
        subject = self._make_verification_subject(pending)
        with tempfile.TemporaryDirectory(prefix="supernova-verify-") as tmpdir:
            subject_path = Path(tmpdir) / "subject.json"
            subject_path.write_text(self._subject_document(subject), encoding="utf-8")
            command = tuple(
                str(subject_path) if arg == SUBJECT_PATH_TOKEN else arg
                for arg in self._verifier_command_template
            )
            result = run_verifier(
                command, timeout_seconds=self._verifier_timeout_seconds
            )

        evidence_id = _verifier_evidence_id(
            subject_sha256=subject.subject_sha256,
            command_template=self._verifier_command_template,
            timeout_seconds=self._verifier_timeout_seconds,
            result=result,
        )
        if result.status is VerifierStatus.PASS:
            outcome = VerificationOutcome.PASS
        elif result.status is VerifierStatus.FAIL:
            outcome = VerificationOutcome.FAIL
        else:
            raise VerificationExecutionError(result.status, evidence_id, result.error)

        return self._apply_verification(
            pending,
            subject,
            outcome=outcome,
            verifier_id=self._verifier_id,
            evidence_id=evidence_id,
        )

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
                verified.product_id,
                verified.step_index,
                verified.content_sha256,
                verified.verification_subject_sha256,
                verified.verification_receipt_sha256,
                VerificationOutcome.PASS,
                verified.producer_id,
                verified.verifier_id,
                verified.evidence_id,
                verified.parent_product_id,
                True,
                False,
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
                verified.product_id,
                verified.step_index,
                verified.content_sha256,
                verified.verification_subject_sha256,
                verified.verification_receipt_sha256,
                VerificationOutcome.PASS,
                verified.producer_id,
                verified.verifier_id,
                verified.evidence_id,
                verified.parent_product_id,
                True,
                True,
            )
        )
        self._last_consumed = verified
        self._verified = None
        self._state = ChainState.COMPLETE
        return verified
