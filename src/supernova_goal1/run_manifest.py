from __future__ import annotations

from collections import Counter, namedtuple
from hashlib import sha256
import json
from secrets import token_bytes
import unicodedata
from typing import Any, Mapping, Sequence

from .assignment import (
    blind_evaluation_order,
    operator_reveal_mapping,
    seeded_paired_assignment,
)
from .contracts import Arm, MODEL_USAGE_BASES, UNFROZEN_MODEL_USAGE_BASIS
from .execution.common import FrozenProblemRequest
from .problem import BenchmarkProblemIdentity


PILOT_MANIFEST_SCHEMA_VERSION = 1
PILOT_MANIFEST_PURPOSE = "NON_CREDIT_PILOT"


def _token(value: object, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field} must be an exact non-empty trimmed string")
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in value):
        raise ValueError(f"{field} must not contain control or format characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must contain only Unicode scalar values") from exc
    return value


def _sha256_hex(value: object, field: str) -> str:
    value = _token(value, field)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field} must be exactly 64 lowercase hexadecimal characters")
    return value


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _non_negative_int(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _arm(value: object) -> Arm:
    if type(value) is Arm:
        return value
    if type(value) is not str:
        raise ValueError(f"unknown arm: {value!r}")
    try:
        return Arm(value)
    except ValueError as exc:
        raise ValueError(f"unknown arm: {value!r}") from exc


def _usage_basis(value: object) -> str:
    allowed = MODEL_USAGE_BASES | {UNFROZEN_MODEL_USAGE_BASIS}
    if type(value) is not str or value not in allowed:
        raise ValueError(
            "model_usage_basis must be provider_tokens, visible_utf8_bytes, "
            "or UNFROZEN"
        )
    return value


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _problem_snapshot(value: object) -> BenchmarkProblemIdentity:
    if type(value) is not BenchmarkProblemIdentity:
        raise TypeError("problem must be an exact BenchmarkProblemIdentity")
    raw = value.to_mapping()
    return BenchmarkProblemIdentity(
        benchmark=raw["benchmark"],
        version=raw["version"],
        split=raw["split"],
        native_id=raw["native_id"],
    )


def _problem_from_mapping(raw: object) -> BenchmarkProblemIdentity:
    if not isinstance(raw, Mapping):
        raise ValueError("problem must be an object")
    expected = {"benchmark", "native_id", "split", "version"}
    if set(raw) != expected:
        raise ValueError(f"problem fields must be exactly {sorted(expected)}")
    return BenchmarkProblemIdentity(
        benchmark=raw["benchmark"],  # type: ignore[arg-type]
        version=raw["version"],  # type: ignore[arg-type]
        split=raw["split"],  # type: ignore[arg-type]
        native_id=raw["native_id"],  # type: ignore[arg-type]
    )


def _new_operator_seed() -> bytes:
    seed = token_bytes(32)
    if type(seed) is not bytes or len(seed) != 32:
        raise RuntimeError("operator CSPRNG must return exactly 32 bytes")
    return seed


_FrozenPilotProblemTuple = namedtuple(
    "FrozenPilotProblem",
    ("problem", "problem_sha256", "family_id"),
    module=__name__,
)


class FrozenPilotProblem(_FrozenPilotProblemTuple):
    """Exact benchmark member and source digest selected before pilot dispatch."""

    __slots__ = ()

    def __new__(
        cls,
        *,
        problem: BenchmarkProblemIdentity,
        problem_sha256: str,
        family_id: str,
    ) -> "FrozenPilotProblem":
        return super().__new__(
            cls,
            _problem_snapshot(problem),
            _sha256_hex(problem_sha256, "problem_sha256"),
            _token(family_id, "family_id"),
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("FrozenPilotProblem may not be subclassed")

    @property
    def problem_id(self) -> str:
        return self.problem.canonical_id

    def to_mapping(self) -> dict[str, object]:
        return {
            "family_id": self.family_id,
            "problem": self.problem.to_mapping(),
            "problem_sha256": self.problem_sha256,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "FrozenPilotProblem":
        if not isinstance(raw, Mapping):
            raise ValueError("frozen pilot problem must be an object")
        expected = {"family_id", "problem", "problem_sha256"}
        if set(raw) != expected:
            raise ValueError(
                f"frozen pilot problem fields must be exactly {sorted(expected)}"
            )
        return cls(
            problem=_problem_from_mapping(raw["problem"]),
            problem_sha256=raw["problem_sha256"],
            family_id=raw["family_id"],
        )


_PilotManifestItemTuple = namedtuple(
    "PilotManifestItem",
    (
        "evaluation_id",
        "problem",
        "problem_sha256",
        "family_id",
        "evaluation_index",
        "attempt_quota",
    ),
    module=__name__,
)


class PilotManifestItem(_PilotManifestItemTuple):
    """Evaluator-visible pilot cell with an opaque arm label."""

    __slots__ = ()

    def __new__(
        cls,
        *,
        evaluation_id: str,
        problem: BenchmarkProblemIdentity,
        problem_sha256: str,
        family_id: str,
        evaluation_index: int,
        attempt_quota: int,
    ) -> "PilotManifestItem":
        return super().__new__(
            cls,
            _token(evaluation_id, "evaluation_id"),
            _problem_snapshot(problem),
            _sha256_hex(problem_sha256, "problem_sha256"),
            _token(family_id, "family_id"),
            _non_negative_int(evaluation_index, "evaluation_index"),
            _positive_int(attempt_quota, "attempt_quota"),
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("PilotManifestItem may not be subclassed")

    @property
    def problem_id(self) -> str:
        return self.problem.canonical_id

    def to_mapping(self) -> dict[str, object]:
        return {
            "attempt_quota": self.attempt_quota,
            "evaluation_id": self.evaluation_id,
            "evaluation_index": self.evaluation_index,
            "family_id": self.family_id,
            "problem": self.problem.to_mapping(),
            "problem_sha256": self.problem_sha256,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PilotManifestItem":
        if not isinstance(raw, Mapping):
            raise ValueError("pilot manifest item must be an object")
        expected = {
            "attempt_quota",
            "evaluation_id",
            "evaluation_index",
            "family_id",
            "problem",
            "problem_sha256",
        }
        if set(raw) != expected:
            raise ValueError(
                f"pilot manifest item fields must be exactly {sorted(expected)}"
            )
        return cls(
            evaluation_id=raw["evaluation_id"],
            problem=_problem_from_mapping(raw["problem"]),
            problem_sha256=raw["problem_sha256"],
            family_id=raw["family_id"],
            evaluation_index=raw["evaluation_index"],
            attempt_quota=raw["attempt_quota"],
        )


_PilotRunManifestTuple = namedtuple(
    "PilotRunManifest",
    (
        "schema_version",
        "purpose",
        "analysis_id",
        "run_id",
        "experiment_id",
        "benchmark_root_sha256",
        "budget_id",
        "budget_sha256",
        "model_usage_basis",
        "runtime_sha256",
        "reveal_commitment_sha256",
        "items",
    ),
    module=__name__,
)


class PilotRunManifest(_PilotRunManifestTuple):
    """Public, non-credit pilot plan. It intentionally contains no arm values."""

    __slots__ = ()

    def __new__(
        cls,
        *,
        analysis_id: str,
        run_id: str,
        experiment_id: str,
        benchmark_root_sha256: str,
        budget_id: str,
        budget_sha256: str,
        model_usage_basis: str,
        runtime_sha256: str,
        reveal_commitment_sha256: str,
        items: Sequence[PilotManifestItem],
    ) -> "PilotRunManifest":
        if isinstance(items, (str, bytes, bytearray)) or not isinstance(
            items, Sequence
        ):
            raise TypeError("items must be an ordered sequence")
        snapshots = tuple(
            PilotManifestItem.from_mapping(item.to_mapping())
            if type(item) is PilotManifestItem
            else (_raise_item_type())
            for item in items
        )
        _validate_public_items(snapshots)
        return super().__new__(
            cls,
            PILOT_MANIFEST_SCHEMA_VERSION,
            PILOT_MANIFEST_PURPOSE,
            _token(analysis_id, "analysis_id"),
            _token(run_id, "run_id"),
            _token(experiment_id, "experiment_id"),
            _sha256_hex(benchmark_root_sha256, "benchmark_root_sha256"),
            _token(budget_id, "budget_id"),
            _sha256_hex(budget_sha256, "budget_sha256"),
            _usage_basis(model_usage_basis),
            _sha256_hex(runtime_sha256, "runtime_sha256"),
            _sha256_hex(
                reveal_commitment_sha256, "reveal_commitment_sha256"
            ),
            snapshots,
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("PilotRunManifest may not be subclassed")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "analysis_id": self.analysis_id,
            "benchmark_root_sha256": self.benchmark_root_sha256,
            "budget_id": self.budget_id,
            "budget_sha256": self.budget_sha256,
            "experiment_id": self.experiment_id,
            "items": [item.to_mapping() for item in self.items],
            "model_usage_basis": self.model_usage_basis,
            "purpose": self.purpose,
            "reveal_commitment_sha256": self.reveal_commitment_sha256,
            "run_id": self.run_id,
            "runtime_sha256": self.runtime_sha256,
            "schema_version": self.schema_version,
        }

    @property
    def manifest_sha256(self) -> str:
        return _canonical_sha256(self._identity_payload())

    def to_mapping(self) -> dict[str, object]:
        raw = self._identity_payload()
        raw["manifest_sha256"] = self.manifest_sha256
        return raw

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PilotRunManifest":
        if not isinstance(raw, Mapping):
            raise ValueError("pilot run manifest must be an object")
        expected = {
            "analysis_id",
            "benchmark_root_sha256",
            "budget_id",
            "budget_sha256",
            "experiment_id",
            "items",
            "manifest_sha256",
            "model_usage_basis",
            "purpose",
            "reveal_commitment_sha256",
            "run_id",
            "runtime_sha256",
            "schema_version",
        }
        if set(raw) != expected:
            raise ValueError(f"pilot manifest fields must be exactly {sorted(expected)}")
        if raw["schema_version"] != PILOT_MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported pilot manifest schema_version")
        if raw["purpose"] != PILOT_MANIFEST_PURPOSE:
            raise ValueError("pilot manifest purpose must be NON_CREDIT_PILOT")
        raw_items = raw["items"]
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError("items must be a non-empty list")
        manifest = cls(
            analysis_id=raw["analysis_id"],
            run_id=raw["run_id"],
            experiment_id=raw["experiment_id"],
            benchmark_root_sha256=raw["benchmark_root_sha256"],
            budget_id=raw["budget_id"],
            budget_sha256=raw["budget_sha256"],
            model_usage_basis=raw["model_usage_basis"],
            runtime_sha256=raw["runtime_sha256"],
            reveal_commitment_sha256=raw["reveal_commitment_sha256"],
            items=tuple(PilotManifestItem.from_mapping(item) for item in raw_items),
        )
        if raw["manifest_sha256"] != manifest.manifest_sha256:
            raise ValueError("manifest_sha256 does not match manifest fields")
        return manifest


def _raise_item_type() -> PilotManifestItem:
    raise TypeError("items must contain exact PilotManifestItem values")


def _validate_public_items(items: tuple[PilotManifestItem, ...]) -> None:
    if not items:
        raise ValueError("items must not be empty")
    if [item.evaluation_index for item in items] != list(range(len(items))):
        raise ValueError("evaluation_index values must be contiguous and ordered from zero")
    if len({item.evaluation_id for item in items}) != len(items):
        raise ValueError("evaluation_id values must be unique")
    if len({item.attempt_quota for item in items}) != 1:
        raise ValueError("attempt quotas must be symmetric across every paired cell")

    counts = Counter(item.problem_id for item in items)
    if any(count != len(Arm) for count in counts.values()):
        raise ValueError("every frozen problem must have exactly one cell per arm")

    problem_snapshots: dict[str, tuple[object, ...]] = {}
    family_to_problem: dict[str, str] = {}
    for item in items:
        snapshot = (item.problem, item.problem_sha256, item.family_id)
        previous = problem_snapshots.setdefault(item.problem_id, snapshot)
        if previous != snapshot:
            raise ValueError("one problem_id cannot carry conflicting frozen snapshots")
        prior_problem = family_to_problem.setdefault(item.family_id, item.problem_id)
        if prior_problem != item.problem_id:
            raise ValueError("pilot manifest may include at most one problem per family")


_PilotRevealEntryTuple = namedtuple(
    "PilotRevealEntry",
    (
        "evaluation_id",
        "assignment_id",
        "arm",
        "execution_index",
        "attempt_quota",
    ),
    module=__name__,
)


class PilotRevealEntry(_PilotRevealEntryTuple):
    """Operator-only mapping from an opaque pilot cell to execution authority."""

    __slots__ = ()

    def __new__(
        cls,
        *,
        evaluation_id: str,
        assignment_id: str,
        arm: Arm | str,
        execution_index: int,
        attempt_quota: int,
    ) -> "PilotRevealEntry":
        return super().__new__(
            cls,
            _token(evaluation_id, "evaluation_id"),
            _token(assignment_id, "assignment_id"),
            _arm(arm),
            _non_negative_int(execution_index, "execution_index"),
            _positive_int(attempt_quota, "attempt_quota"),
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("PilotRevealEntry may not be subclassed")

    def to_mapping(self) -> dict[str, object]:
        return {
            "arm": self.arm.value,
            "assignment_id": self.assignment_id,
            "attempt_quota": self.attempt_quota,
            "evaluation_id": self.evaluation_id,
            "execution_index": self.execution_index,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PilotRevealEntry":
        if not isinstance(raw, Mapping):
            raise ValueError("pilot reveal entry must be an object")
        expected = {
            "arm",
            "assignment_id",
            "attempt_quota",
            "evaluation_id",
            "execution_index",
        }
        if set(raw) != expected:
            raise ValueError(
                f"pilot reveal entry fields must be exactly {sorted(expected)}"
            )
        return cls(
            evaluation_id=raw["evaluation_id"],
            assignment_id=raw["assignment_id"],
            arm=raw["arm"],
            execution_index=raw["execution_index"],
            attempt_quota=raw["attempt_quota"],
        )


_PilotOperatorRevealMapTuple = namedtuple(
    "PilotOperatorRevealMap",
    ("manifest_sha256", "seed_hex", "entries"),
    module=__name__,
)


class PilotOperatorRevealMap(_PilotOperatorRevealMapTuple):
    """Separately serialized operator-only arm reveal for one public manifest."""

    __slots__ = ()

    def __new__(
        cls,
        *,
        manifest_sha256: str,
        seed_hex: str,
        entries: Sequence[PilotRevealEntry],
    ) -> "PilotOperatorRevealMap":
        if isinstance(entries, (str, bytes, bytearray)) or not isinstance(
            entries, Sequence
        ):
            raise TypeError("entries must be an ordered sequence")
        snapshots = tuple(
            PilotRevealEntry.from_mapping(entry.to_mapping())
            if type(entry) is PilotRevealEntry
            else (_raise_reveal_type())
            for entry in entries
        )
        _validate_reveal_entries(snapshots)
        return super().__new__(
            cls,
            _sha256_hex(manifest_sha256, "manifest_sha256"),
            _sha256_hex(seed_hex, "seed_hex"),
            snapshots,
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("PilotOperatorRevealMap may not be subclassed")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "entries": [entry.to_mapping() for entry in self.entries],
            "manifest_sha256": self.manifest_sha256,
            "seed_hex": self.seed_hex,
        }

    @property
    def reveal_sha256(self) -> str:
        return _canonical_sha256(self._identity_payload())

    def to_mapping(self) -> dict[str, object]:
        raw = self._identity_payload()
        raw["reveal_sha256"] = self.reveal_sha256
        return raw

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PilotOperatorRevealMap":
        if not isinstance(raw, Mapping):
            raise ValueError("pilot operator reveal map must be an object")
        expected = {"entries", "manifest_sha256", "reveal_sha256", "seed_hex"}
        if set(raw) != expected:
            raise ValueError(f"pilot reveal fields must be exactly {sorted(expected)}")
        raw_entries = raw["entries"]
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ValueError("entries must be a non-empty list")
        reveal = cls(
            manifest_sha256=raw["manifest_sha256"],
            seed_hex=raw["seed_hex"],
            entries=tuple(PilotRevealEntry.from_mapping(item) for item in raw_entries),
        )
        if raw["reveal_sha256"] != reveal.reveal_sha256:
            raise ValueError("reveal_sha256 does not match reveal fields")
        return reveal

    def validate_for(self, manifest: PilotRunManifest) -> None:
        if type(manifest) is not PilotRunManifest:
            raise TypeError("manifest must be an exact PilotRunManifest")
        manifest = PilotRunManifest.from_mapping(manifest.to_mapping())
        reveal = PilotOperatorRevealMap.from_mapping(self.to_mapping())
        if reveal.manifest_sha256 != manifest.manifest_sha256:
            raise ValueError("reveal does not bind the supplied manifest")
        reveal_commitment = _canonical_sha256(
            [entry.to_mapping() for entry in reveal.entries]
        )
        if reveal_commitment != manifest.reveal_commitment_sha256:
            raise ValueError(
                "reveal entries do not match the manifest reveal commitment"
            )

        public_by_id = {item.evaluation_id: item for item in manifest.items}
        reveal_by_id = {entry.evaluation_id: entry for entry in reveal.entries}
        if set(public_by_id) != set(reveal_by_id):
            raise ValueError("reveal must cover exactly every public evaluation_id")
        if any(
            public_by_id[evaluation_id].attempt_quota != entry.attempt_quota
            for evaluation_id, entry in reveal_by_id.items()
        ):
            raise ValueError("reveal attempt quota does not match public manifest")

        seed = bytes.fromhex(reveal.seed_hex)
        assignments = seeded_paired_assignment(
            sorted({item.problem_id for item in manifest.items}), seed
        )
        blind_order = blind_evaluation_order(assignments, seed)
        derived_reveal = operator_reveal_mapping(assignments, seed)
        assignment_by_id = {
            assignment.assignment_id: assignment for assignment in assignments
        }
        assignment_id_by_evaluation = {
            entry.evaluation_id: entry.assignment_id
            for entry in derived_reveal.entries
        }
        if tuple(item.evaluation_id for item in blind_order.items) != tuple(
            item.evaluation_id for item in manifest.items
        ):
            raise ValueError("public manifest does not match the operator seed")
        expected_entries = tuple(
            PilotRevealEntry(
                evaluation_id=item.evaluation_id,
                assignment_id=assignment_id_by_evaluation[item.evaluation_id],
                arm=assignment_by_id[
                    assignment_id_by_evaluation[item.evaluation_id]
                ].arm,
                execution_index=assignment_by_id[
                    assignment_id_by_evaluation[item.evaluation_id]
                ].execution_index,
                attempt_quota=item.attempt_quota,
            )
            for item in manifest.items
        )
        if reveal.entries != expected_entries:
            raise ValueError("reveal entries do not match the operator seed")

        arms_by_problem: dict[str, set[Arm]] = {}
        for evaluation_id, entry in reveal_by_id.items():
            problem_id = public_by_id[evaluation_id].problem_id
            arms = arms_by_problem.setdefault(problem_id, set())
            if entry.arm in arms:
                raise ValueError(f"duplicate revealed arm for problem {problem_id}")
            arms.add(entry.arm)
        if any(arms != set(Arm) for arms in arms_by_problem.values()):
            raise ValueError("revealed plan is not a complete five-arm pairing")

    def validate_request(
        self,
        manifest: PilotRunManifest,
        *,
        evaluation_id: str,
        request: FrozenProblemRequest,
    ) -> None:
        """Validate one exact pre-dispatch request against this frozen pilot plan."""

        self.validate_for(manifest)
        evaluation_id = _token(evaluation_id, "evaluation_id")
        if type(request) is not FrozenProblemRequest:
            raise TypeError("request must be an exact FrozenProblemRequest")
        request = FrozenProblemRequest.from_mapping(request.to_mapping())

        public_by_id = {item.evaluation_id: item for item in manifest.items}
        reveal_by_id = {entry.evaluation_id: entry for entry in self.entries}
        if evaluation_id not in public_by_id:
            raise ValueError("evaluation_id is not present in the pilot manifest")
        item = public_by_id[evaluation_id]
        reveal = reveal_by_id[evaluation_id]
        expected = (
            manifest.run_id,
            manifest.experiment_id,
            item.problem,
            manifest.benchmark_root_sha256,
            item.problem_sha256,
            reveal.arm,
            manifest.budget_id,
            manifest.budget_sha256,
            manifest.model_usage_basis,
            manifest.runtime_sha256,
        )
        actual = (
            request.run_id,
            request.experiment_id,
            request.problem,
            request.benchmark_root_sha256,
            request.problem_sha256,
            request.arm,
            request.budget_id,
            request.budget_sha256,
            request.model_usage_basis,
            request.runtime_sha256,
        )
        if actual != expected:
            raise ValueError("request does not match the frozen pilot cell")
        if request.attempt >= reveal.attempt_quota:
            raise ValueError("request attempt exceeds the frozen pilot quota")


def _raise_reveal_type() -> PilotRevealEntry:
    raise TypeError("entries must contain exact PilotRevealEntry values")


def _validate_reveal_entries(entries: tuple[PilotRevealEntry, ...]) -> None:
    if not entries:
        raise ValueError("entries must not be empty")
    if len({entry.evaluation_id for entry in entries}) != len(entries):
        raise ValueError("reveal evaluation_id values must be unique")
    if len({entry.assignment_id for entry in entries}) != len(entries):
        raise ValueError("reveal assignment_id values must be unique")
    if sorted(entry.execution_index for entry in entries) != list(range(len(entries))):
        raise ValueError("execution_index values must be contiguous from zero")
    if len({entry.attempt_quota for entry in entries}) != 1:
        raise ValueError("attempt quotas must be symmetric across every paired cell")


def _validate_frozen_problems(
    problems: Sequence[FrozenPilotProblem],
) -> tuple[FrozenPilotProblem, ...]:
    if isinstance(problems, (str, bytes, bytearray)) or not isinstance(
        problems, Sequence
    ):
        raise TypeError("problems must be an ordered sequence")
    snapshots = tuple(
        FrozenPilotProblem.from_mapping(problem.to_mapping())
        if type(problem) is FrozenPilotProblem
        else (_raise_problem_type())
        for problem in problems
    )
    if not snapshots:
        raise ValueError("problems must not be empty")
    if len({problem.problem_id for problem in snapshots}) != len(snapshots):
        raise ValueError("problem identities must be unique")
    if len({problem.family_id for problem in snapshots}) != len(snapshots):
        raise ValueError("pilot manifest may include at most one problem per family")
    benchmark_contract = {
        (
            problem.problem.benchmark,
            problem.problem.version,
            problem.problem.split,
        )
        for problem in snapshots
    }
    if len(benchmark_contract) != 1:
        raise ValueError("every pilot problem must belong to one benchmark/version/split")
    return snapshots


def _raise_problem_type() -> FrozenPilotProblem:
    raise TypeError("problems must contain exact FrozenPilotProblem values")


def generate_seeded_paired_pilot_manifest(
    *,
    analysis_id: str,
    run_id: str,
    experiment_id: str,
    problems: Sequence[FrozenPilotProblem],
    benchmark_root_sha256: str,
    budget_id: str,
    budget_sha256: str,
    model_usage_basis: str,
    runtime_sha256: str,
    attempts_per_cell: int,
) -> tuple[PilotRunManifest, PilotOperatorRevealMap]:
    """Build a deterministic paired pilot plan and a separate operator reveal.

    The public manifest is explicitly non-credit and carries no arm values. The reveal
    is a distinct content-addressed artifact that must remain outside the evaluator
    boundary. Dispatch code can expand each revealed cell into attempt indices
    ``0..attempt_quota-1`` and register those requests before execution.
    """

    frozen_problems = _validate_frozen_problems(problems)
    attempts_per_cell = _positive_int(attempts_per_cell, "attempts_per_cell")
    seed = _new_operator_seed()
    problem_by_id = {problem.problem_id: problem for problem in frozen_problems}

    assignments = seeded_paired_assignment(problem_by_id, seed)
    blind_order = blind_evaluation_order(assignments, seed)
    base_reveal = operator_reveal_mapping(assignments, seed)
    assignment_by_id = {assignment.assignment_id: assignment for assignment in assignments}
    assignment_id_by_evaluation = {
        entry.evaluation_id: entry.assignment_id for entry in base_reveal.entries
    }

    items = tuple(
        PilotManifestItem(
            evaluation_id=item.evaluation_id,
            problem=problem_by_id[item.problem_id].problem,
            problem_sha256=problem_by_id[item.problem_id].problem_sha256,
            family_id=problem_by_id[item.problem_id].family_id,
            evaluation_index=item.evaluation_index,
            attempt_quota=attempts_per_cell,
        )
        for item in blind_order.items
    )
    reveal_entries = tuple(
        PilotRevealEntry(
            evaluation_id=item.evaluation_id,
            assignment_id=assignment_id_by_evaluation[item.evaluation_id],
            arm=assignment_by_id[
                assignment_id_by_evaluation[item.evaluation_id]
            ].arm,
            execution_index=assignment_by_id[
                assignment_id_by_evaluation[item.evaluation_id]
            ].execution_index,
            attempt_quota=attempts_per_cell,
        )
        for item in blind_order.items
    )
    reveal_commitment_sha256 = _canonical_sha256(
        [entry.to_mapping() for entry in reveal_entries]
    )
    manifest = PilotRunManifest(
        analysis_id=analysis_id,
        run_id=run_id,
        experiment_id=experiment_id,
        benchmark_root_sha256=benchmark_root_sha256,
        budget_id=budget_id,
        budget_sha256=budget_sha256,
        model_usage_basis=model_usage_basis,
        runtime_sha256=runtime_sha256,
        reveal_commitment_sha256=reveal_commitment_sha256,
        items=items,
    )
    reveal = PilotOperatorRevealMap(
        manifest_sha256=manifest.manifest_sha256,
        seed_hex=seed.hex(),
        entries=reveal_entries,
    )
    reveal.validate_for(manifest)
    return manifest, reveal
