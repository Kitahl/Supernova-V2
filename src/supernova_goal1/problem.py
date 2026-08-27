from __future__ import annotations

from collections import namedtuple
from collections.abc import Sequence
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


_BenchmarkProblemIdentityTuple = namedtuple(
    "BenchmarkProblemIdentity", ("benchmark", "version", "split", "native_id"), module=__name__
)


class BenchmarkProblemIdentity(_BenchmarkProblemIdentityTuple):
    """Stable, tuple-immutable identity for one problem in one exact benchmark split."""

    __slots__ = ()
    benchmark: str
    version: str
    split: str
    native_id: str

    def __new__(
        cls,
        benchmark: str,
        version: str,
        split: str,
        native_id: str,
    ) -> "BenchmarkProblemIdentity":
        return super().__new__(
            cls,
            _token(benchmark, "benchmark"),
            _token(version, "version"),
            _token(split, "split"),
            _token(native_id, "native_id"),
        )

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


_SplitContractTuple = namedtuple(
    "SplitContract", ("benchmark", "version", "split", "problems"), module=__name__
)


class SplitContract(_SplitContractTuple):
    """Immutable, ordered membership contract for one benchmark split."""

    __slots__ = ()
    benchmark: str
    version: str
    split: str
    problems: tuple[BenchmarkProblemIdentity, ...]

    def __new__(
        cls,
        benchmark: str,
        version: str,
        split: str,
        problems: Sequence[BenchmarkProblemIdentity],
    ) -> "SplitContract":
        benchmark = _token(benchmark, "benchmark")
        version = _token(version, "version")
        split = _token(split, "split")
        if not isinstance(problems, Sequence) or isinstance(
            problems, (str, bytes, bytearray)
        ):
            raise TypeError("problems must be an ordered sequence")
        frozen_problems = tuple(problems)
        if not frozen_problems:
            raise ValueError("problems must contain at least one benchmark problem")

        canonical_ids: set[str] = set()
        for problem in frozen_problems:
            if not isinstance(problem, BenchmarkProblemIdentity):
                raise TypeError("problems must contain BenchmarkProblemIdentity values")
            if (
                problem.benchmark,
                problem.version,
                problem.split,
            ) != (benchmark, version, split):
                raise ValueError("every problem must belong to the contract benchmark/version/split")
            if problem.canonical_id in canonical_ids:
                raise ValueError(f"duplicate problem identity: {problem.canonical_id}")
            canonical_ids.add(problem.canonical_id)

        return super().__new__(cls, benchmark, version, split, frozen_problems)

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
