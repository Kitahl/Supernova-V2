from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
from typing import Callable

from ..contracts import Arm
from ..dispatch import CompletionRecord, DispatchAuthority, DispatchManifest
from ..verifier import VerifierStatus
from .baselines import (
    BaselineDispatch,
    BaselineExecution,
    ModelAttemptObservation,
    VerifierCall,
    _execute_baseline_attempt,
)
from .common import AttemptStatus, FrozenProblemRequest


_PRODUCT_PREFIX = (
    b"-- supernova-kind: VERIFIED_PRODUCT\n"
    b"-- supernova-schema: supernova.verified-product-emission.v1\n"
)


def _utf8(value: bytes, field: str) -> bytes:
    if type(value) is not bytes:
        raise TypeError(f"{field} must be exact bytes")
    try:
        value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{field} must be valid UTF-8") from exc
    return value


def _token(value: str, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field} must be an exact non-empty trimmed string")
    return value


def _sha256_hex(value: str, field: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{field} must be 64 lowercase hexadecimal characters")
    return value


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def render_verified_product_emission(product_utf8: bytes) -> bytes:
    """Return one signed response that is both classified and directly Lean-verifiable.

    The discriminator is a fixed pair of Lean comments. The verifier receives these
    exact bytes; no hidden unwrap can make the receipt name a different artifact.
    """

    product = _utf8(product_utf8, "product_utf8")
    if not product:
        raise ValueError("product_utf8 must not be empty")
    return _PRODUCT_PREFIX + product


def _parse_verified_product_emission(response_utf8: bytes) -> bytes:
    response = _utf8(response_utf8, "verified product response")
    if not response.startswith(_PRODUCT_PREFIX):
        raise ValueError("verified product response discriminator is invalid")
    product = response[len(_PRODUCT_PREFIX) :]
    if not product:
        raise ValueError("verified product response content must not be empty")
    return product


def _same_frozen_cell(left: FrozenProblemRequest, right: FrozenProblemRequest) -> bool:
    return (
        left.run_id == right.run_id
        and left.experiment_id == right.experiment_id
        and left.problem.to_mapping() == right.problem.to_mapping()
        and left.benchmark_root_sha256 == right.benchmark_root_sha256
        and left.problem_sha256 == right.problem_sha256
        and left.arm is right.arm
        and left.budget_id == right.budget_id
        and left.budget_sha256 == right.budget_sha256
        and left.model_usage_basis == right.model_usage_basis
        and left.runtime_sha256 == right.runtime_sha256
    )


@dataclass(frozen=True)
class RetryLink:
    """Exact signed terminal predecessor; registration alone cannot authorize retry."""

    predecessor_completion: CompletionRecord

    def __post_init__(self) -> None:
        if type(self.predecessor_completion) is not CompletionRecord:
            raise TypeError(
                "predecessor_completion must be an exact CompletionRecord"
            )
        completion = CompletionRecord.from_mapping(
            self.predecessor_completion.to_mapping()
        )
        payload = completion.payload
        if payload.request.arm is not Arm.VERIFIED_CHAIN:
            raise ValueError("retry predecessor must be a verified-chain completion")
        result = payload.attempt_result
        receipt = payload.verifier_receipt
        if result.status is AttemptStatus.ANSWERED:
            if receipt is None:
                raise ValueError("answered retry predecessor requires verifier evidence")
            if receipt.status is VerifierStatus.PASS:
                raise ValueError(
                    "a Lean-PASS completion feeds forward and is not retry-eligible"
                )
        object.__setattr__(self, "predecessor_completion", completion)

    @property
    def attempt(self) -> int:
        return self.predecessor_completion.payload.request.attempt

    @property
    def dispatch_id(self) -> str:
        return self.predecessor_completion.dispatch_id

    @property
    def frozen_request_sha256(self) -> str:
        return self.predecessor_completion.payload.request.frozen_request_sha256

    def to_mapping(self) -> dict[str, object]:
        payload = self.predecessor_completion.payload
        receipt = payload.verifier_receipt
        return {
            "attempt": self.attempt,
            "completion_sha256": self.predecessor_completion.record_sha256,
            "dispatch_id": self.dispatch_id,
            "frozen_request_sha256": self.frozen_request_sha256,
            "status": payload.attempt_result.status.value,
            "verifier_receipt_sha256": (
                None if receipt is None else receipt.receipt_sha256
            ),
        }


@dataclass(frozen=True)
class AdmittedProduct:
    """A product whose exact signed visible response received a Lean PASS receipt."""

    producer_completion: CompletionRecord
    producer_response_utf8: bytes

    def __post_init__(self) -> None:
        if type(self.producer_completion) is not CompletionRecord:
            raise TypeError("producer_completion must be an exact CompletionRecord")
        completion = CompletionRecord.from_mapping(self.producer_completion.to_mapping())
        response = _utf8(self.producer_response_utf8, "producer_response_utf8")
        payload = completion.payload
        request = payload.request
        result = payload.attempt_result
        receipt = payload.verifier_receipt
        if request.arm is not Arm.VERIFIED_CHAIN:
            raise ValueError("admitted products require verified-chain completions")
        if result.status is not AttemptStatus.ANSWERED:
            raise ValueError("admitted product producer must be an ANSWERED attempt")
        if receipt is None or receipt.status is not VerifierStatus.PASS:
            raise ValueError("admitted products require an exact Lean PASS receipt")
        receipt.validate_for(request, result)
        if not result.response_artifact.verifies(response):
            raise ValueError("producer response bytes do not match signed completion")
        _parse_verified_product_emission(response)
        object.__setattr__(self, "producer_completion", completion)
        object.__setattr__(self, "producer_response_utf8", bytes(response))

    @property
    def content_utf8(self) -> bytes:
        return _parse_verified_product_emission(self.producer_response_utf8)

    @property
    def product_id(self) -> str:
        return f"sha256:{sha256(self.producer_response_utf8).hexdigest()}"

    @property
    def producer_attempt(self) -> int:
        return self.producer_completion.payload.request.attempt

    @property
    def producer_frozen_request_sha256(self) -> str:
        return self.producer_completion.payload.request.frozen_request_sha256

    def to_mapping(self) -> dict[str, object]:
        payload = self.producer_completion.payload
        receipt = payload.verifier_receipt
        assert receipt is not None
        return {
            "content_utf8": self.content_utf8.decode("utf-8"),
            "product_id": self.product_id,
            "producer_attempt": self.producer_attempt,
            "producer_candidate_artifact_id": receipt.candidate_artifact_id,
            "producer_completion_sha256": self.producer_completion.record_sha256,
            "producer_dispatch_id": self.producer_completion.dispatch_id,
            "producer_frozen_request_sha256": self.producer_frozen_request_sha256,
            "producer_response_sha256": sha256(self.producer_response_utf8).hexdigest(),
            "verifier_receipt_sha256": receipt.receipt_sha256,
            "verification": "LEAN_PASS",
        }


class VerifiedChainObservationKind(StrEnum):
    PRODUCT = "PRODUCT"
    ANSWERED = "ANSWERED"
    NO_ANSWER = "NO_ANSWER"
    ERROR = "ERROR"


@dataclass(frozen=True)
class VerifiedChainObservation:
    """Trusted-host classification of one visible scheduled-chat response."""

    dispatch_id: str
    kind: VerifiedChainObservationKind
    response_utf8: bytes
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "dispatch_id", _sha256_hex(self.dispatch_id, "dispatch_id"))
        try:
            kind = VerifiedChainObservationKind(self.kind)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown verified-chain observation kind: {self.kind!r}") from exc
        object.__setattr__(self, "kind", kind)
        response = _utf8(self.response_utf8, "response_utf8")
        object.__setattr__(self, "response_utf8", bytes(response))
        if kind in {
            VerifiedChainObservationKind.PRODUCT,
            VerifiedChainObservationKind.ANSWERED,
        }:
            if not response:
                raise ValueError(f"{kind.value} observation requires visible response bytes")
            if self.error is not None:
                raise ValueError(f"{kind.value} observation cannot carry error")
            if kind is VerifiedChainObservationKind.PRODUCT:
                _parse_verified_product_emission(response)
            elif response.startswith(_PRODUCT_PREFIX):
                raise ValueError("ANSWERED observation cannot carry a PRODUCT discriminator")
        elif kind is VerifiedChainObservationKind.NO_ANSWER:
            if response:
                raise ValueError("NO_ANSWER observation must have an empty response")
            if self.error is not None:
                raise ValueError("NO_ANSWER observation cannot carry error")
        elif type(self.error) is not str or not self.error.strip():
            raise ValueError("ERROR observation requires a non-empty error")


VerifiedChainModelCall = Callable[
    [BaselineDispatch, bytes],
    VerifiedChainObservation,
]


@dataclass(frozen=True)
class VerifiedChainStepExecution:
    baseline: BaselineExecution
    visible_product_ids: tuple[str, ...]
    retry_of: RetryLink | None
    admitted_product: AdmittedProduct | None
    terminal_answer: bool

    def __post_init__(self) -> None:
        if type(self.baseline) is not BaselineExecution:
            raise TypeError("baseline must be an exact BaselineExecution")
        if self.baseline.completion.payload.request.arm is not Arm.VERIFIED_CHAIN:
            raise ValueError("verified-chain step must carry a verified-chain completion")
        if type(self.visible_product_ids) is not tuple or not all(
            type(value) is str and value.startswith("sha256:")
            for value in self.visible_product_ids
        ):
            raise TypeError("visible_product_ids must be exact content addresses")
        if self.retry_of is not None and type(self.retry_of) is not RetryLink:
            raise TypeError("retry_of must be an exact RetryLink or null")
        if self.admitted_product is not None:
            if type(self.admitted_product) is not AdmittedProduct:
                raise TypeError("admitted_product must be an exact AdmittedProduct or null")
            request = self.baseline.completion.payload.request
            if (
                self.admitted_product.producer_frozen_request_sha256
                != request.frozen_request_sha256
                or self.admitted_product.producer_attempt != request.attempt
            ):
                raise ValueError("admitted product does not match producer request")
        if type(self.terminal_answer) is not bool:
            raise TypeError("terminal_answer must be boolean")
        if self.admitted_product is not None and self.terminal_answer:
            raise ValueError("one step cannot be both an admitted product and terminal answer")


def render_verified_chain_request(
    problem_prompt_utf8: bytes,
    *,
    admitted_products: tuple[AdmittedProduct, ...],
    retry_of: RetryLink | None,
) -> bytes:
    """Freeze the complete and exclusive verified-chain visibility boundary."""

    prompt = _utf8(problem_prompt_utf8, "problem_prompt_utf8")
    if type(admitted_products) is not tuple or not all(
        type(product) is AdmittedProduct for product in admitted_products
    ):
        raise TypeError("admitted_products must contain exact AdmittedProduct values")
    if retry_of is not None and type(retry_of) is not RetryLink:
        raise TypeError("retry_of must be an exact RetryLink or null")
    snapshots = tuple(
        AdmittedProduct(product.producer_completion, product.producer_response_utf8)
        for product in admitted_products
    )
    product_ids = [product.product_id for product in snapshots]
    dispatch_ids = [product.producer_completion.dispatch_id for product in snapshots]
    if len(product_ids) != len(set(product_ids)):
        raise ValueError("admitted product_ids must be unique")
    if len(dispatch_ids) != len(set(dispatch_ids)):
        raise ValueError("admitted producer dispatches must be unique")
    retry = (
        None
        if retry_of is None
        else RetryLink(retry_of.predecessor_completion)
    )
    return _canonical_bytes(
        {
            "admitted_products": [product.to_mapping() for product in snapshots],
            "problem_prompt_utf8": prompt.decode("utf-8"),
            "retry_of": None if retry is None else retry.to_mapping(),
            "schema": "supernova.verified-chain-visible-request.v1",
        }
    )


def _authenticate_retry(
    authority: DispatchAuthority,
    manifest: DispatchManifest,
    current: FrozenProblemRequest,
    retry_of: RetryLink,
) -> None:
    predecessor_completion = retry_of.predecessor_completion
    predecessor_request = predecessor_completion.payload.request
    if retry_of.attempt >= current.attempt:
        raise ValueError("retry predecessor must be an earlier frozen attempt")
    matches = [
        entry
        for entry in manifest.entries
        if (
            entry.dispatch_id == predecessor_completion.dispatch_id
            and entry.entry_sha256 == predecessor_completion.entry_sha256
            and entry.request_sha256 == retry_of.frozen_request_sha256
            and entry.attempt_index == retry_of.attempt
        )
    ]
    if len(matches) != 1:
        raise ValueError("retry predecessor is not uniquely present in supplied manifest")
    entry = matches[0]
    connection = authority._connect()
    try:
        requests = authority._requests_from_db(connection)
        stored_request = requests.get(entry.dispatch_id)
        if stored_request is None:
            raise ValueError("retry predecessor completion is absent from authority")
        authority._validate_record_for_entry(
            connection,
            entry,
            predecessor_completion,
            stored_request,
        )
        stored_request = FrozenProblemRequest.from_mapping(
            stored_request.to_mapping()
        )
    finally:
        connection.close()
    if stored_request.frozen_request_sha256 != retry_of.frozen_request_sha256:
        raise ValueError("retry predecessor request digest does not match authority")
    if (
        predecessor_request.frozen_request_sha256
        != stored_request.frozen_request_sha256
    ):
        raise ValueError("retry completion request does not match authority")
    if not _same_frozen_cell(stored_request, current):
        raise ValueError("retry predecessor is outside the current frozen cell")


def execute_verified_chain_step(
    *,
    authority: DispatchAuthority,
    manifest: DispatchManifest,
    request: FrozenProblemRequest,
    problem_prompt_utf8: bytes,
    admitted_products: tuple[AdmittedProduct, ...],
    retry_of: RetryLink | None,
    model_call: VerifiedChainModelCall,
    verifier_call: VerifierCall,
) -> VerifiedChainStepExecution:
    """Execute one preregistered step and admit only an exact Lean-PASS product."""

    if type(authority) is not DispatchAuthority:
        raise TypeError("authority must be an exact DispatchAuthority")
    if type(manifest) is not DispatchManifest:
        raise TypeError("manifest must be an exact DispatchManifest")
    if type(request) is not FrozenProblemRequest:
        raise TypeError("request must be an exact FrozenProblemRequest")
    request = FrozenProblemRequest.from_mapping(request.to_mapping())
    if request.arm is not Arm.VERIFIED_CHAIN:
        raise ValueError("verified-chain adapter requires a verified-chain request")
    if type(admitted_products) is not tuple or not all(
        type(product) is AdmittedProduct for product in admitted_products
    ):
        raise TypeError("admitted_products must contain exact AdmittedProduct values")
    if retry_of is not None and type(retry_of) is not RetryLink:
        raise TypeError("retry_of must be an exact RetryLink or null")

    canonical_request = render_verified_chain_request(
        problem_prompt_utf8,
        admitted_products=admitted_products,
        retry_of=retry_of,
    )
    if not request.request_artifact.verifies(canonical_request):
        raise ValueError("frozen request artifact does not match admitted-product visibility")

    product_ids: set[str] = set()
    producer_dispatches: set[str] = set()
    for product in admitted_products:
        if product.product_id in product_ids:
            raise ValueError("admitted product_ids must be unique")
        if product.producer_completion.dispatch_id in producer_dispatches:
            raise ValueError("admitted producer dispatches must be unique")
        product_ids.add(product.product_id)
        producer_dispatches.add(product.producer_completion.dispatch_id)
        if product.producer_attempt >= request.attempt:
            raise ValueError("admitted products must come from an earlier frozen attempt")
        producer_request = _authenticate_admitted_product(authority, manifest, product)
        if not _same_frozen_cell(producer_request, request):
            raise ValueError("admitted product producer is outside the current frozen cell")

    if retry_of is not None:
        _authenticate_retry(authority, manifest, request, retry_of)

    captured: list[VerifiedChainObservation] = []

    def adapt_model_call(
        dispatch: BaselineDispatch,
        request_utf8: bytes,
    ) -> ModelAttemptObservation:
        observation = model_call(dispatch, request_utf8)
        if type(observation) is not VerifiedChainObservation:
            raise TypeError("model_call must return VerifiedChainObservation")
        observation = VerifiedChainObservation(
            observation.dispatch_id,
            observation.kind,
            observation.response_utf8,
            observation.error,
        )
        captured.append(observation)
        status = {
            VerifiedChainObservationKind.PRODUCT: AttemptStatus.ANSWERED,
            VerifiedChainObservationKind.ANSWERED: AttemptStatus.ANSWERED,
            VerifiedChainObservationKind.NO_ANSWER: AttemptStatus.NO_ANSWER,
            VerifiedChainObservationKind.ERROR: AttemptStatus.ERROR,
        }[observation.kind]
        return ModelAttemptObservation(
            observation.dispatch_id,
            observation.response_utf8,
            status,
            observation.error,
        )

    baseline = _execute_baseline_attempt(
        expected_arm=Arm.VERIFIED_CHAIN,
        authority=authority,
        manifest=manifest,
        request=request,
        request_utf8=canonical_request,
        model_call=adapt_model_call,
        verifier_call=verifier_call,
    )

    admitted_product = None
    terminal_answer = False
    if captured and captured[0].dispatch_id == baseline.completion.dispatch_id:
        result = baseline.completion.payload.attempt_result
        receipt = baseline.completion.payload.verifier_receipt
        if (
            captured[0].kind is VerifiedChainObservationKind.PRODUCT
            and result.status is AttemptStatus.ANSWERED
            and receipt is not None
            and receipt.status is VerifierStatus.PASS
            and result.response_artifact.verifies(captured[0].response_utf8)
        ):
            admitted_product = AdmittedProduct(
                baseline.completion,
                captured[0].response_utf8,
            )
        elif (
            captured[0].kind is VerifiedChainObservationKind.ANSWERED
            and result.status is AttemptStatus.ANSWERED
            and receipt is not None
            and receipt.status is VerifierStatus.PASS
        ):
            terminal_answer = True

    return VerifiedChainStepExecution(
        baseline,
        tuple(product.product_id for product in admitted_products),
        retry_of,
        admitted_product,
        terminal_answer,
    )


__all__ = [
    "AdmittedProduct",
    "RetryLink",
    "VerifiedChainObservation",
    "VerifiedChainObservationKind",
    "VerifiedChainStepExecution",
    "execute_verified_chain_step",
    "render_verified_chain_request",
    "render_verified_product_emission",
]
