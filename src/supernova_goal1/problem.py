from __future__ import annotations

from collections import namedtuple
from collections.abc import Sequence
from hashlib import sha256
import json
import unicodedata


def _token(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a non-empty trimmed string")
    # Authority-bearing tokens must not retain behavior from a user-defined str
    # subclass (custom equality/strip/encode could otherwise spoof validation).
    value = str.__str__(value)
    if not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty trimmed string")
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in value):
        raise ValueError(f"{field} must not contain control or format characters")
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

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("BenchmarkProblemIdentity may not be subclassed")

    def __eq__(self, other: object) -> bool:
        if type(other) is not BenchmarkProblemIdentity:
            return False
        return tuple.__eq__(self, other)

    def __ne__(self, other: object) -> bool:
        if type(other) is not BenchmarkProblemIdentity:
            return True
        return tuple.__ne__(self, other)

    __hash__ = tuple.__hash__

    @classmethod
    def _make(cls, iterable: object) -> "BenchmarkProblemIdentity":
        return cls(*tuple(iterable))  # type: ignore[arg-type]

    def _validated_fields(self) -> tuple[str, str, str, str]:
        values = tuple(self)
        if len(values) != 4:
            raise ValueError("problem identity storage must contain exactly four fields")
        benchmark, version, split, native_id = values
        return (
            _token(benchmark, "benchmark"),
            _token(version, "version"),
            _token(split, "split"),
            _token(native_id, "native_id"),
        )

    @property
    def canonical_id(self) -> str:
        return f"sha256:{_digest(self.to_mapping())}"

    def to_mapping(self) -> dict[str, str]:
        benchmark, version, split, native_id = self._validated_fields()
        return {
            "benchmark": benchmark,
            "native_id": native_id,
            "split": split,
            "version": version,
        }


def _validated_problems(
    benchmark: str,
    version: str,
    split: str,
    problems: Sequence[BenchmarkProblemIdentity],
) -> tuple[BenchmarkProblemIdentity, ...]:
    if not isinstance(problems, Sequence) or isinstance(
        problems, (str, bytes, bytearray)
    ):
        raise TypeError("problems must be an ordered sequence")
    frozen_problems = tuple(problems)
    if not frozen_problems:
        raise ValueError("problems must contain at least one benchmark problem")

    canonical_ids: set[str] = set()
    for problem in frozen_problems:
        if type(problem) is not BenchmarkProblemIdentity:
            raise TypeError("problems must contain exact BenchmarkProblemIdentity values")
        problem_benchmark, problem_version, problem_split, _ = problem._validated_fields()
        if (
            problem_benchmark,
            problem_version,
            problem_split,
        ) != (benchmark, version, split):
            raise ValueError("every problem must belong to the contract benchmark/version/split")
        canonical_id = problem.canonical_id
        if canonical_id in canonical_ids:
            raise ValueError(f"duplicate problem identity: {canonical_id}")
        canonical_ids.add(canonical_id)
    return frozen_problems


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
        frozen_problems = _validated_problems(benchmark, version, split, problems)
        return super().__new__(cls, benchmark, version, split, frozen_problems)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("SplitContract may not be subclassed")

    def __eq__(self, other: object) -> bool:
        if type(other) is not SplitContract:
            return False
        return tuple.__eq__(self, other)

    def __ne__(self, other: object) -> bool:
        if type(other) is not SplitContract:
            return True
        return tuple.__ne__(self, other)

    __hash__ = tuple.__hash__

    @classmethod
    def _make(cls, iterable: object) -> "SplitContract":
        return cls(*tuple(iterable))  # type: ignore[arg-type]

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

    def _validated_fields(
        self,
    ) -> tuple[str, str, str, tuple[BenchmarkProblemIdentity, ...]]:
        values = tuple(self)
        if len(values) != 4:
            raise ValueError("split contract storage must contain exactly four fields")
        benchmark, version, split, problems = values
        benchmark = _token(benchmark, "benchmark")
        version = _token(version, "version")
        split = _token(split, "split")
        if type(problems) is not tuple:
            raise TypeError("split contract storage must contain an immutable problem tuple")
        return benchmark, version, split, _validated_problems(
            benchmark, version, split, problems
        )

    @property
    def problem_ids(self) -> tuple[str, ...]:
        _, _, _, problems = self._validated_fields()
        return tuple(problem.canonical_id for problem in problems)

    @property
    def contract_id(self) -> str:
        return f"sha256:{_digest(self.to_mapping())}"

    def to_mapping(self) -> dict[str, object]:
        benchmark, version, split, problems = self._validated_fields()
        return {
            "benchmark": benchmark,
            "problem_ids": [problem.canonical_id for problem in problems],
            "split": split,
            "version": version,
        }
