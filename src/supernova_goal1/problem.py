from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
import json


def _token(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty trimmed string")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{field} must not contain control characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must contain only Unicode scalar values") from exc
    return value


def _digest(payload: object) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class BenchmarkProblemIdentity:
    """Stable identity for one problem in one exact benchmark split."""

    benchmark: str
    version: str
    split: str
    native_id: str

    def __post_init__(self) -> None:
        for field in ("benchmark", "version", "split", "native_id"):
            object.__setattr__(self, field, _token(getattr(self, field), field))

    @property
    def canonical_id(self) -> str:
        return f"sha256:{_digest(self.to_mapping())}"

    def to_mapping(self) -> dict[str, str]:
        return {
            "benchmark": self.benchmark,
            "native_id": self.native_id,
            "split": self.split,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class SplitContract:
    """Immutable, ordered membership contract for one benchmark split."""

    benchmark: str
    version: str
    split: str
    problems: tuple[BenchmarkProblemIdentity, ...]

    def __post_init__(self) -> None:
        for field in ("benchmark", "version", "split"):
            object.__setattr__(self, field, _token(getattr(self, field), field))
        if not isinstance(self.problems, Sequence) or isinstance(
            self.problems, (str, bytes, bytearray)
        ):
            raise TypeError("problems must be an ordered sequence")
        object.__setattr__(self, "problems", tuple(self.problems))
        if not self.problems:
            raise ValueError("problems must contain at least one benchmark problem")

        canonical_ids: set[str] = set()
        for problem in self.problems:
            if not isinstance(problem, BenchmarkProblemIdentity):
                raise TypeError("problems must contain BenchmarkProblemIdentity values")
            if (
                problem.benchmark,
                problem.version,
                problem.split,
            ) != (self.benchmark, self.version, self.split):
                raise ValueError("every problem must belong to the contract benchmark/version/split")
            if problem.canonical_id in canonical_ids:
                raise ValueError(f"duplicate problem identity: {problem.canonical_id}")
            canonical_ids.add(problem.canonical_id)

    @classmethod
    def from_native_ids(
        cls,
        *,
        benchmark: str,
        version: str,
        split: str,
        native_ids: Sequence[str],
    ) -> "SplitContract":
        if isinstance(native_ids, (str, bytes, bytearray)):
            raise TypeError("native_ids must be a sequence of problem-id strings")
        if not isinstance(native_ids, Sequence):
            raise TypeError("native_ids must be an ordered sequence")
        frozen_ids = tuple(native_ids)
        return cls(
            benchmark=benchmark,
            version=version,
            split=split,
            problems=tuple(
                BenchmarkProblemIdentity(
                    benchmark=benchmark,
                    version=version,
                    split=split,
                    native_id=native_id,
                )
                for native_id in frozen_ids
            ),
        )

    @property
    def problem_ids(self) -> tuple[str, ...]:
        return tuple(problem.canonical_id for problem in self.problems)

    @property
    def contract_id(self) -> str:
        return f"sha256:{_digest(self.to_mapping())}"

    def to_mapping(self) -> dict[str, object]:
        return {
            "benchmark": self.benchmark,
            "problem_ids": list(self.problem_ids),
            "split": self.split,
            "version": self.version,
        }
