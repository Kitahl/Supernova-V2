from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
from typing import Callable

from ..contracts import Arm
from ..dispatch import CompletionRecord, DispatchAuthority, DispatchManifest
from .baselines import (
    BaselineDispatch,
    BaselineExecution,
    ModelAttemptObservation,
    VerifierCall,
    _execute_baseline_attempt,
)
from .common import AttemptStatus, FrozenProblemRequest


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


def _attempt(value: int | None, field: str) -> int | None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{field} must be a non-negative integer or null")
    return value


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


@dataclass(frozen=True)
class VisibleProduct:
    """One immutable unverified product bound to its signed producer completion."""

    producer_completion: CompletionRecord
    content_utf8: bytes

    def __post_init__(self) -> None:
        if type(self.producer_completion) is not CompletionRecord:
            raise TypeError("producer_completion must be an exact CompletionRecord")
        completion = CompletionRecord.from_mapping(
            self.producer_completion.to_mapping()
        )
        content = _utf8(self.content_utf8, "content_utf8")
        if not content:
            raise ValueError("visible product content must not be empty")
        payload = completion.payload
        result = payload.attempt_result
        if payload.request.arm is not Arm.PRODUCT_ONLY:
            raise ValueError("visible products require product-only producer completions")
        if result.status is not AttemptStatus.NO_ANSWER:
            raise ValueError("visible product producer must be a non-terminal product step")
        if payload.verifier_receipt is not None:
            raise ValueError("visible products must be unverified")
        if not result.response_artifact.verifies(content):
            raise ValueError("visible product bytes do not match producer completion")
        object.__setattr__(self, "producer_completion", completion)
        object.__setattr__(self, "content_utf8", bytes(content))

    @property
    def producer_frozen_request_sha256(self) -> str:
        return self.producer_completion.payload.request.frozen_request_sha256

    @property
    def producer_attempt(self) -> int:
        return self.producer_completion.payload.request.attempt

    @property
    def product_id(self) -> str:
        return f"sha256:{sha256(self.content_utf8).hexdigest()}"

    def to_mapping(self) -> dict[str, object]:
        return {
            "content_utf8": self.content_utf8.decode("utf-8"),
            "product_id": self.product_id,
            "producer_attempt": self.producer_attempt,
            "producer_completion_sha256": self.producer_completion.record_sha256,
            "producer_dispatch_id": self.producer_completion.dispatch_id,
            "producer_frozen_request_sha256": self.producer_frozen_request_sha256,
            "verification": "UNVERIFIED",
        }


@dataclass(frozen=True)
class FidelityStage:
    stage_id: str
    fidelity_rank: int
    retry_of_attempt: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage_id", _token(self.stage_id, "stage_id"))
        if type(self.fidelity_rank) is not int or self.fidelity_rank < 0:
            raise ValueError("fidelity_rank must be a non-negative integer")
        object.__setattr__(
            self,
            "retry_of_attempt",
            _attempt(self.retry_of_attempt, "retry_of_attempt"),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "fidelity_rank": self.fidelity_rank,
            "retry_of_attempt": self.retry_of_attempt,
            "stage_id": self.stage_id,
        }


class ProductObservationKind(StrEnum):
    PRODUCT = "PRODUCT"
    ANSWERED = "ANSWERED"
    NO_ANSWER = "NO_ANSWER"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ProductControlObservation:
    """Visible response classification from the trusted scheduled-chat host."""

    dispatch_id: str
    kind: ProductObservationKind
    response_utf8: bytes
    error: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.dispatch_id) is not str
            or len(self.dispatch_id) != 64
            or any(char not in "0123456789abcdef" for char in self.dispatch_id)
        ):
            raise ValueError("dispatch_id must be 64 lowercase hexadecimal characters")
        try:
            kind = ProductObservationKind(self.kind)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown product observation kind: {self.kind!r}") from exc
        object.__setattr__(self, "kind", kind)
        response = _utf8(self.response_utf8, "response_utf8")
        object.__setattr__(self, "response_utf8", bytes(response))
        if kind in {ProductObservationKind.PRODUCT, ProductObservationKind.ANSWERED}:
            if not response:
                raise ValueError(f"{kind.value} observation requires visible response bytes")
            if self.error is not None:
                raise ValueError(f"{kind.value} observation cannot carry error")
        elif kind is ProductObservationKind.NO_ANSWER:
            if self.error is not None:
                raise ValueError("NO_ANSWER observation cannot carry error")
        elif type(self.error) is not str or not self.error.strip():
            raise ValueError("ERROR observation requires a non-empty error")


ProductModelCall = Callable[
    [BaselineDispatch, bytes],
    ProductControlObservation,
]


@dataclass(frozen=True)
class ProductOnlyStepExecution:
    baseline: BaselineExecution
    visible_product_ids: tuple[str, ...]
    retry_of_attempt: int | None
    emitted_product: VisibleProduct | None

    def __post_init__(self) -> None:
        if type(self.baseline) is not BaselineExecution:
            raise TypeError("baseline must be an exact BaselineExecution")
        if self.baseline.completion.payload.request.arm is not Arm.PRODUCT_ONLY:
            raise ValueError("product-only step must carry a product-only completion")
        if type(self.visible_product_ids) is not tuple or not all(
            type(value) is str and value.startswith("sha256:")
            for value in self.visible_product_ids
        ):
            raise TypeError("visible_product_ids must be an exact tuple of content addresses")
        object.__setattr__(
            self,
            "retry_of_attempt",
            _attempt(self.retry_of_attempt, "retry_of_attempt"),
        )
        if self.emitted_product is not None:
            if type(self.emitted_product) is not VisibleProduct:
                raise TypeError("emitted_product must be an exact VisibleProduct or null")
            request = self.baseline.completion.payload.request
            if (
                self.emitted_product.producer_frozen_request_sha256
                != request.frozen_request_sha256
                or self.emitted_product.producer_attempt != request.attempt
            ):
                raise ValueError("emitted product does not match producer request")


@dataclass(frozen=True)
class MultiFidelityStageExecution:
    baseline: BaselineExecution
    stage: FidelityStage

    def __post_init__(self) -> None:
        if type(self.baseline) is not BaselineExecution:
            raise TypeError("baseline must be an exact BaselineExecution")
        if type(self.stage) is not FidelityStage:
            raise TypeError("stage must be an exact FidelityStage")
        if self.baseline.completion.payload.request.arm is not Arm.MULTI_FIDELITY:
            raise ValueError("stage execution must carry a multi-fidelity completion")


def render_product_only_request(
    problem_prompt_utf8: bytes,
    *,
    visible_products: tuple[VisibleProduct, ...],
    retry_of_attempt: int | None,
) -> bytes:
    """Render the only accepted product-only model-visible request shape."""

    prompt = _utf8(problem_prompt_utf8, "problem_prompt_utf8")
    if type(visible_products) is not tuple or not all(
        type(product) is VisibleProduct for product in visible_products
    ):
        raise TypeError("visible_products must contain exact VisibleProduct values")
    retry = _attempt(retry_of_attempt, "retry_of_attempt")
    snapshots = tuple(
        VisibleProduct(
            product.producer_completion,
            product.content_utf8,
        )
        for product in visible_products
    )
    ids = [product.product_id for product in snapshots]
    if len(ids) != len(set(ids)):
        raise ValueError("visible product_ids must be unique")
    return _canonical_bytes(
        {
            "problem_prompt_utf8": prompt.decode("utf-8"),
            "retry_of_attempt": retry,
            "schema": "supernova.product-only-visible-request.v1",
            "visible_products": [product.to_mapping() for product in snapshots],
        }
    )


def render_multi_fidelity_request(
    problem_prompt_utf8: bytes,
    *,
    stage: FidelityStage,
) -> bytes:
    """Render the only accepted multi-fidelity request shape; no products enter."""

    prompt = _utf8(problem_prompt_utf8, "problem_prompt_utf8")
    if type(stage) is not FidelityStage:
        raise TypeError("stage must be an exact FidelityStage")
    stage = FidelityStage(
        stage.stage_id,
        stage.fidelity_rank,
        stage.retry_of_attempt,
    )
    return _canonical_bytes(
        {
            "problem_prompt_utf8": prompt.decode("utf-8"),
            "schema": "supernova.multi-fidelity-stage-request.v1",
            "stage": stage.to_mapping(),
            "visible_products": [],
        }
    )


def _validate_retry(
    request: FrozenProblemRequest,
    retry_of_attempt: int | None,
) -> None:
    retry = _attempt(retry_of_attempt, "retry_of_attempt")
    if retry is not None and retry >= request.attempt:
        raise ValueError("retry_of_attempt must precede the current frozen attempt")


def _authenticate_visible_product(
    authority: DispatchAuthority,
    manifest: DispatchManifest,
    product: VisibleProduct,
) -> None:
    """Verify one producer record using the dispatch authority's stored request/key."""

    producer = product.producer_completion
    matches = [
        entry
        for entry in manifest.entries
        if (
            entry.dispatch_id == producer.dispatch_id
            and entry.entry_sha256 == producer.entry_sha256
        )
    ]
    if len(matches) != 1:
        raise ValueError(
            "visible product producer completion is absent from the supplied manifest"
        )
    entry = matches[0]
    # DispatchAuthority owns the Lamport-key verification implementation. This
    # read-only validation intentionally uses the same authority path as close()
    # without consuming the still-open multi-attempt run.
    connection = authority._connect()
    try:
        stored_requests = authority._requests_from_db(connection)
        stored_request = stored_requests.get(entry.dispatch_id)
        if stored_request is None:
            raise ValueError("visible product producer request is absent from authority")
        authority._validate_record_for_entry(
            connection,
            entry,
            producer,
            stored_request,
        )
    finally:
        connection.close()


def execute_product_only_step(
    *,
    authority: DispatchAuthority,
    manifest: DispatchManifest,
    request: FrozenProblemRequest,
    problem_prompt_utf8: bytes,
    visible_products: tuple[VisibleProduct, ...],
    retry_of_attempt: int | None,
    model_call: ProductModelCall,
    verifier_call: VerifierCall,
) -> ProductOnlyStepExecution:
    """Execute one product-only step with a fully explicit visibility boundary."""

    if type(request) is not FrozenProblemRequest:
        raise TypeError("request must be an exact FrozenProblemRequest")
    request = FrozenProblemRequest.from_mapping(request.to_mapping())
    if request.arm is not Arm.PRODUCT_ONLY:
        raise ValueError("product-only adapter requires a product-only request")
    _validate_retry(request, retry_of_attempt)
    canonical_request = render_product_only_request(
        problem_prompt_utf8,
        visible_products=visible_products,
        retry_of_attempt=retry_of_attempt,
    )
    for product in visible_products:
        if product.producer_attempt >= request.attempt:
            raise ValueError("visible products must come from an earlier frozen attempt")
        _authenticate_visible_product(authority, manifest, product)
        producer_request = product.producer_completion.payload.request
        if (
            producer_request.run_id != request.run_id
            or producer_request.problem_id != request.problem_id
            or producer_request.arm is not Arm.PRODUCT_ONLY
        ):
            raise ValueError(
                "visible product producer is outside the current product-only cell"
            )
    if not request.request_artifact.verifies(canonical_request):
        raise ValueError(
            "frozen request artifact does not match explicit product visibility"
        )

    captured: list[ProductControlObservation] = []

    def adapt_model_call(
        dispatch: BaselineDispatch,
        request_utf8: bytes,
    ) -> ModelAttemptObservation:
        observation = model_call(dispatch, request_utf8)
        if type(observation) is not ProductControlObservation:
            raise TypeError("model_call must return ProductControlObservation")
        observation = ProductControlObservation(
            observation.dispatch_id,
            observation.kind,
            observation.response_utf8,
            observation.error,
        )
        captured.append(observation)
        status = {
            ProductObservationKind.PRODUCT: AttemptStatus.NO_ANSWER,
            ProductObservationKind.ANSWERED: AttemptStatus.ANSWERED,
            ProductObservationKind.NO_ANSWER: AttemptStatus.NO_ANSWER,
            ProductObservationKind.ERROR: AttemptStatus.ERROR,
        }[observation.kind]
        return ModelAttemptObservation(
            observation.dispatch_id,
            observation.response_utf8,
            status,
            observation.error,
        )

    baseline = _execute_baseline_attempt(
        expected_arm=Arm.PRODUCT_ONLY,
        authority=authority,
        manifest=manifest,
        request=request,
        request_utf8=canonical_request,
        model_call=adapt_model_call,
        verifier_call=verifier_call,
    )
    emitted_product = None
    if captured and captured[0].kind is ProductObservationKind.PRODUCT:
        result = baseline.completion.payload.attempt_result
        if (
            result.status is AttemptStatus.NO_ANSWER
            and captured[0].dispatch_id == baseline.completion.dispatch_id
            and result.response_artifact.verifies(captured[0].response_utf8)
        ):
            emitted_product = VisibleProduct(
                baseline.completion,
                captured[0].response_utf8,
            )
    return ProductOnlyStepExecution(
        baseline,
        tuple(product.product_id for product in visible_products),
        retry_of_attempt,
        emitted_product,
    )


def execute_multi_fidelity_stage(
    *,
    authority: DispatchAuthority,
    manifest: DispatchManifest,
    request: FrozenProblemRequest,
    problem_prompt_utf8: bytes,
    stage: FidelityStage,
    model_call: Callable[[BaselineDispatch, bytes], ModelAttemptObservation],
    verifier_call: VerifierCall,
) -> MultiFidelityStageExecution:
    """Execute one explicit stage; retry and fidelity boundaries are frozen bytes."""

    if type(request) is not FrozenProblemRequest:
        raise TypeError("request must be an exact FrozenProblemRequest")
    request = FrozenProblemRequest.from_mapping(request.to_mapping())
    if request.arm is not Arm.MULTI_FIDELITY:
        raise ValueError("multi-fidelity adapter requires a multi-fidelity request")
    if type(stage) is not FidelityStage:
        raise TypeError("stage must be an exact FidelityStage")
    stage = FidelityStage(
        stage.stage_id,
        stage.fidelity_rank,
        stage.retry_of_attempt,
    )
    _validate_retry(request, stage.retry_of_attempt)
    canonical_request = render_multi_fidelity_request(
        problem_prompt_utf8,
        stage=stage,
    )
    if not request.request_artifact.verifies(canonical_request):
        raise ValueError(
            "frozen request artifact does not match explicit fidelity stage"
        )
    baseline = _execute_baseline_attempt(
        expected_arm=Arm.MULTI_FIDELITY,
        authority=authority,
        manifest=manifest,
        request=request,
        request_utf8=canonical_request,
        model_call=model_call,
        verifier_call=verifier_call,
    )
    return MultiFidelityStageExecution(baseline, stage)


__all__ = [
    "FidelityStage",
    "MultiFidelityStageExecution",
    "ProductControlObservation",
    "ProductObservationKind",
    "ProductOnlyStepExecution",
    "VisibleProduct",
    "execute_multi_fidelity_stage",
    "execute_product_only_step",
    "render_multi_fidelity_request",
    "render_product_only_request",
]
