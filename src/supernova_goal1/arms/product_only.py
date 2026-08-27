from __future__ import annotations

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
    product_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for field in (
            "request_id",
            "experiment_id",
            "problem_id",
            "budget_id",
            "problem_statement",
        ):
            _text(getattr(self, field), field)
        if not isinstance(self.product_ids, tuple):
            raise ValueError("product_ids must be a tuple")
        if not self.product_ids:
            raise ValueError("product-only requires at least one product")
        for index, product_id in enumerate(self.product_ids):
            _text(product_id, f"product_ids[{index}]")
        if len(set(self.product_ids)) != len(self.product_ids):
            raise ValueError("product_ids must be unique")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ProductOnlyRequest":
        expected = {
            "request_id",
            "experiment_id",
            "problem_id",
            "budget_id",
            "problem_statement",
            "product_ids",
        }
        _exact_fields(raw, expected, "product-only request")
        product_ids = raw["product_ids"]
        if not isinstance(product_ids, list):
            raise ValueError("product_ids must be a JSON list")
        return cls(
            request_id=raw["request_id"],
            experiment_id=raw["experiment_id"],
            problem_id=raw["problem_id"],
            budget_id=raw["budget_id"],
            problem_statement=raw["problem_statement"],
            product_ids=tuple(product_ids),
        )


@dataclass(frozen=True)
class ProductOnlyProduct:
    product_id: str
    parent_product_id: str | None
    value: str

    def __post_init__(self) -> None:
        _text(self.product_id, "product_id")
        if self.parent_product_id is not None:
            _text(self.parent_product_id, "parent_product_id")
        _text(self.value, "value")

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
                    "ANSWERED product-only result must consume the final unverified product"
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

        result_ids = tuple(product.product_id for product in self.products)
        requested_prefix = request.product_ids[: len(result_ids)]
        if result_ids != requested_prefix:
            raise ValueError(
                "product-only result products must be an ordered prefix of request product_ids"
            )
        if self.status is ProductOnlyStatus.ANSWERED and result_ids != request.product_ids:
            raise ValueError("ANSWERED product-only result must complete every requested product")
