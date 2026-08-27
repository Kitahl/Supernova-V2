from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


def _exact_fields(raw: Mapping[str, Any], expected: set[str], prefix: str) -> None:
    if set(raw) != expected:
        raise ValueError(f"{prefix} fields must be exactly {sorted(expected)}")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _positive(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


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


def _canonical_product_snapshot(value: Any) -> tuple[str, str]:
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
    return canonical_json, hashlib.sha256(canonical_bytes).hexdigest()


class ProductOnlyStatus(StrEnum):
    ANSWERED = "ANSWERED"
    NO_ANSWER = "NO_ANSWER"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ProductOnlyRequest:
    request_id: str
    experiment_id: str
    problem_id: str
    budget_id: str
    problem_statement: str
    max_products: int

    def __post_init__(self) -> None:
        for field in (
            "request_id",
            "experiment_id",
            "problem_id",
            "budget_id",
            "problem_statement",
        ):
            _text(getattr(self, field), field)
        _positive(self.max_products, "max_products")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ProductOnlyRequest":
        expected = {
            "request_id",
            "experiment_id",
            "problem_id",
            "budget_id",
            "problem_statement",
            "max_products",
        }
        _exact_fields(raw, expected, "product-only request")
        return cls(
            request_id=raw["request_id"],
            experiment_id=raw["experiment_id"],
            problem_id=raw["problem_id"],
            budget_id=raw["budget_id"],
            problem_statement=raw["problem_statement"],
            max_products=raw["max_products"],
        )


@dataclass(frozen=True, init=False)
class ProductOnlyProduct:
    product_id: str
    parent_product_id: str | None
    canonical_json: str
    content_sha256: str

    def __init__(self, product_id: str, parent_product_id: str | None, value: Any) -> None:
        _text(product_id, "product_id")
        if parent_product_id is not None:
            _text(parent_product_id, "parent_product_id")
        canonical_json, content_sha256 = _canonical_product_snapshot(value)
        object.__setattr__(self, "product_id", product_id)
        object.__setattr__(self, "parent_product_id", parent_product_id)
        object.__setattr__(self, "canonical_json", canonical_json)
        object.__setattr__(self, "content_sha256", content_sha256)

    @property
    def value(self) -> Any:
        return json.loads(self.canonical_json)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ProductOnlyProduct":
        expected = {"product_id", "parent_product_id", "value"}
        _exact_fields(raw, expected, "product-only product")
        return cls(
            product_id=raw["product_id"],
            parent_product_id=raw["parent_product_id"],
            value=raw["value"],
        )


@dataclass(frozen=True)
class ProductOnlyResult:
    request_id: str
    experiment_id: str
    problem_id: str
    budget_id: str
    products: tuple[ProductOnlyProduct, ...]
    status: ProductOnlyStatus
    answer: str | None
    answer_parent_product_id: str | None
    error: str | None

    def __post_init__(self) -> None:
        for field in ("request_id", "experiment_id", "problem_id", "budget_id"):
            _text(getattr(self, field), field)
        if not isinstance(self.products, tuple):
            raise ValueError("products must be a tuple")
        if not all(isinstance(product, ProductOnlyProduct) for product in self.products):
            raise ValueError("products must contain ProductOnlyProduct values")

        product_ids = [product.product_id for product in self.products]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("product-only product_ids must be unique")
        for index, product in enumerate(self.products):
            expected_parent = None if index == 0 else self.products[index - 1].product_id
            if product.parent_product_id != expected_parent:
                raise ValueError("product-only products must form one contiguous unverified chain")

        try:
            status = ProductOnlyStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown product-only status: {self.status!r}") from exc
        object.__setattr__(self, "status", status)

        if status is ProductOnlyStatus.ANSWERED:
            if not self.products:
                raise ValueError("ANSWERED product-only result requires at least one product")
            _text(self.answer, "answer")
            if self.error is not None:
                raise ValueError("ANSWERED product-only result cannot carry error")
            if self.answer_parent_product_id != self.products[-1].product_id:
                raise ValueError(
                    "ANSWERED product-only result must consume the current final unverified product"
                )
        elif status is ProductOnlyStatus.NO_ANSWER:
            if (
                self.answer is not None
                or self.answer_parent_product_id is not None
                or self.error is not None
            ):
                raise ValueError(
                    "NO_ANSWER product-only result carries neither answer, answer parent, nor error"
                )
        else:
            if self.answer is not None or self.answer_parent_product_id is not None:
                raise ValueError("ERROR product-only result cannot carry an answer")
            _text(self.error, "error")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ProductOnlyResult":
        expected = {
            "request_id",
            "experiment_id",
            "problem_id",
            "budget_id",
            "products",
            "status",
            "answer",
            "answer_parent_product_id",
            "error",
        }
        _exact_fields(raw, expected, "product-only result")
        products = raw["products"]
        if not isinstance(products, list):
            raise ValueError("products must be a JSON list")
        parsed_products = []
        for item in products:
            if not isinstance(item, Mapping):
                raise ValueError("product-only product must be an object")
            parsed_products.append(ProductOnlyProduct.from_mapping(item))
        return cls(
            request_id=raw["request_id"],
            experiment_id=raw["experiment_id"],
            problem_id=raw["problem_id"],
            budget_id=raw["budget_id"],
            products=tuple(parsed_products),
            status=raw["status"],
            answer=raw["answer"],
            answer_parent_product_id=raw["answer_parent_product_id"],
            error=raw["error"],
        )

    def validate_for(self, request: ProductOnlyRequest) -> None:
        bindings = (
            ("request_id", self.request_id, request.request_id),
            ("experiment_id", self.experiment_id, request.experiment_id),
            ("problem_id", self.problem_id, request.problem_id),
            ("budget_id", self.budget_id, request.budget_id),
        )
        for field, actual, expected in bindings:
            if actual != expected:
                raise ValueError(f"product-only result {field} does not match request")

        if len(self.products) > request.max_products:
            raise ValueError("product-only result exceeds request max_products")
