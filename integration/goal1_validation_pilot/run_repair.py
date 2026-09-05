"""Bounded NON-CREDIT archive replay and synthetic product-transfer diagnostic.

Default execution makes zero model calls. Candidate Lean is sent only through
ProductionVerifierPort's keyless sandbox; it is never executed on the host.
Historical results are observations, not fixture gates or retroactive credit.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sqlite3
import sys
import time
import uuid
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from integration.goal1_validation_pilot import run_validation_pilot as pilot
from integration.goal1_validation_pilot.pilot_prompt import build_pilot_product_prompt
from supernova_goal1.confirmatory_io import product_declaration_name
from supernova_goal1.verifier_evidence import VerifierEvidenceRecord

REPAIR_PLAN_PATH = Path(__file__).with_name("REPAIR_PLAN.json")
ARCHIVE_PREFIX = "Supernova-V2/Supernova-V2/validation-one-percent-output/"
REPORT_MEMBER = ARCHIVE_PREFIX + "one-percent-report.json"
DATABASE_MEMBER = ARCHIVE_PREFIX + "verifier-evidence.sqlite3"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_DATABASE_BYTES = 64 * 1024 * 1024
MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_MODEL_CALLS = 2
REVIEW_SCOPE = "NON_CREDIT_LOCAL_REPAIR_GATE_NOT_SCIENTIFIC_AUTHORITY"
FIXTURE_GATES = (
    "known_valid_fenced",
    "prose_invalid",
    "product_parser_and_verifier",
    "byte_exact_product_exposure",
    "final_explicit_product_use_valid",
)


class RepairGateError(RuntimeError):
    """An integrity or fixture gate that prevents further model dispatch."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RepairGateError(message)


def _error(exc: Exception) -> dict[str, Any]:
    value: dict[str, Any] = {"type": type(exc).__name__, "message": str(exc)[:4096]}
    diagnostic = getattr(exc, "diagnostic", None)
    if diagnostic is not None and hasattr(diagnostic, "to_dict"):
        value["model_container_diagnostic"] = diagnostic.to_dict()
    return value


def _source(
    native_id: str, source: bytes, provenance: str
) -> pilot.FrozenLeanProblemSource:
    return pilot.FrozenLeanProblemSource.from_record(
        {
            "schema_version": 1,
            "problem_id": native_id,
            "split": "validation",
            "source_id": provenance,
            "source_record_sha256": pilot.sha256(source),
            "lean_code_sha256": pilot.sha256(source),
            "lean_code": source.decode("utf-8"),
            "informal_prefix": "",
        },
        expected_split="validation",
    )


def synthetic_source() -> pilot.FrozenLeanProblemSource:
    return _source(
        "supernova_repair_nat_refl",
        b"import Mathlib\n\ntheorem supernova_repair_nat_refl (n : Nat) : n = n := by\n",
        "SYNTHETIC_NON_CREDIT_NAT_REFLEXIVITY_NOT_BENCHMARK",
    )


@dataclass(frozen=True)
class ArchivedResponse:
    source: pilot.FrozenLeanProblemSource
    attempt: int
    raw_completion: bytes
    original_candidate: bytes
    original_record_sha256: str
    original_verdict: str
    original_dispatch_id: str


def _member(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    entries = [entry for entry in archive.infolist() if entry.filename == name]
    _require(len(entries) == 1, f"archive member must be unique: {name}")
    _require(
        entries[0].file_size <= limit, f"archive member exceeds byte limit: {name}"
    )
    return archive.read(entries[0])


def load_archive(
    path: Path,
    *,
    plan: dict[str, Any],
    input_directory: Path,
) -> tuple[list[ArchivedResponse], dict[str, Any]]:
    """Bind zip and report before parsing; open an exact DB copy read-only.

    The archived WAL-mode SQLite header prevents an in-memory deserialize read.
    A byte-identical copy opened with mode=ro&immutable=1 avoids rewriting its
    journal header. No archive paths are extracted or trusted as output paths.
    Historical signatures cannot be reverified: the old report retained only a
    public-key hash. The pinned archive, record, and blob digests are checked.
    """
    _require(path.stat().st_size <= MAX_ARCHIVE_BYTES, "archive exceeds byte limit")
    raw = path.read_bytes()
    expected = plan["historical_artifact"]
    _require(pilot.sha256(raw) == expected["zip_sha256"], "archive SHA256 mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        report_raw = _member(archive, REPORT_MEMBER, MAX_REPORT_BYTES)
        _require(
            pilot.sha256(report_raw) == expected["report_sha256"],
            "archive report SHA256 mismatch",
        )
        report = json.loads(report_raw)
        _require(
            report["plan_sha256"] == plan["historical_plan_sha256"],
            "historical plan digest mismatch",
        )
        database_raw = _member(archive, DATABASE_MEMBER, MAX_DATABASE_BYTES)
    rows = [
        row
        for row in report["attempts"]
        if row["arm"] == "ordinary"
        and row["attempt_status"] == "ANSWERED"
        and row["response_kind"] == "FINAL_ANSWER"
    ]
    _require(
        len(rows) == 5,
        "archive must contain exactly five classified ordinary responses",
    )
    input_directory.mkdir(exist_ok=False)
    database_path = input_directory / "verifier-evidence.sqlite3"
    database_path.write_bytes(database_raw)
    connection = sqlite3.connect(
        database_path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
    )
    result = []
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        for row in rows:
            record_hash = row["verifier_evidence"]["record_sha256"]
            records = connection.execute(
                "SELECT body_json,signature_b64,source_blob,candidate_blob "
                "FROM verifier_evidence WHERE record_sha256=?",
                (record_hash,),
            ).fetchall()
            _require(
                len(records) == 1,
                "archive signed evidence record is absent or duplicated",
            )
            body_raw, signature, source_blob, candidate_blob = records[0]
            record = VerifierEvidenceRecord(
                body_json=bytes(body_raw), signature_b64=signature
            )
            _require(
                record.record_sha256 == record_hash, "archive record digest mismatch"
            )
            body = record.body
            raw_completion = row["raw_completion_utf8"].encode("utf-8")
            candidate = row["candidate_utf8"].encode("utf-8")
            _require(
                len(raw_completion) == row["raw_completion_bytes"]
                and pilot.sha256(raw_completion) == row["raw_completion_sha256"],
                "archive raw completion digest mismatch",
            )
            _require(
                candidate == bytes(candidate_blob)
                and len(candidate) == row["candidate_bytes"]
                and pilot.sha256(candidate)
                == row["candidate_sha256"]
                == body["binding"]["candidate_source_sha256"],
                "archive original candidate binding mismatch",
            )
            source = _source(
                row["problem_id"],
                bytes(source_blob),
                "DIGEST_BOUND_ARCHIVE_SOURCE_BLOB",
            )
            _require(
                source.source_sha256 == body["binding"]["source_construction_sha256"]
                and source.theorem_statement_sha256
                == body["binding"]["theorem_statement_sha256"]
                and row["dispatch_id"] == body["binding"]["actual_dispatch_id"]
                and row["attempt"] == body["binding"]["attempt_id"]
                and body["binding"]["arm_id"] == "ordinary",
                "archive source or dispatch binding mismatch",
            )
            _require(
                row["verifier_evidence"]["verdict"] == body["observations"]["verdict"],
                "archive verdict mismatch",
            )
            result.append(
                ArchivedResponse(
                    source,
                    row["attempt"],
                    raw_completion,
                    candidate,
                    record_hash,
                    body["observations"]["verdict"],
                    row["dispatch_id"],
                )
            )
    finally:
        connection.close()
    _require(
        len({item.original_dispatch_id for item in result}) == 5,
        "archive responses are duplicated",
    )
    return result, {
        "status": "PASS",
        "zip_sha256": pilot.sha256(raw),
        "report_sha256": pilot.sha256(report_raw),
        "database_sha256": pilot.sha256(database_raw),
        "ordinary_returned_proofs": len(result),
        "original_archive_modified": False,
        "historical_signature_authentication": "NOT_REVERIFIABLE_OLD_PUBLIC_KEY_NOT_RETAINED",
        "source_provenance": "ORIGINAL_SQLITE_SOURCE_BLOB_RECONSTRUCTED_METADATA",
    }


def current_code_digests() -> dict[str, str]:
    paths = set((ROOT / "src" / "supernova_goal1").rglob("*.py"))
    paths.update(Path(__file__).parent.glob("*.py"))
    paths.update(
        (
            REPAIR_PLAN_PATH,
            pilot.PLAN_PATH,
            pilot.BASELINES_PATH,
            pilot.PRODUCT_CONTROLS_PATH,
            pilot.VERIFIER_PUBLICATION_PATH,
        )
    )
    return {
        path.relative_to(ROOT).as_posix(): pilot.sha256(path.read_bytes())
        for path in sorted(paths)
    }


def validate_review_evidence(path: Path) -> dict[str, Any]:
    value = pilot.load_object(path)
    _require(
        value.get("schema") == "supernova.goal1.repair-review-evidence.v1",
        "review evidence schema changed",
    )
    _require(value.get("scope") == REVIEW_SCOPE, "review evidence scope changed")
    _require(
        value.get("source_sha256") == current_code_digests(),
        "review evidence does not bind current code",
    )
    for key, field in (("unit_tests", "command"), ("independent_review", "reviewer")):
        gate = value.get(key)
        _require(
            type(gate) is dict and gate.get("status") == "PASS", f"{key} has not passed"
        )
        _require(
            type(gate.get(field)) is str and bool(gate[field].strip()),
            f"{key} lacks {field}",
        )
        artifact = Path(gate["evidence_path"])
        if not artifact.is_absolute():
            artifact = path.parent / artifact
        _require(
            pilot.sha256(artifact.read_bytes()) == gate["evidence_sha256"],
            f"{key} artifact digest mismatch",
        )
    return {
        "status": "PASS",
        "scope": REVIEW_SCOPE,
        "evidence_sha256": pilot.sha256(path.read_bytes()),
        "attestation": value,
    }


class RealVerifier:
    def __init__(self, output_directory: Path, plan: dict[str, Any], plan_sha256: str):
        self.plan_sha256 = plan_sha256
        self.run_id = "g1-non-credit-repair-" + uuid.uuid4().hex
        self.sequence = 0
        self.signer = pilot.HostVerifierSigner(
            issuer_id="goal1-non-credit-repair-host",
            signing_key_id=self.run_id + "-key",
            private_key=os.urandom(32),
        )
        self.launcher = pilot.verifier_launcher(
            {
                "timing_policy": {
                    "outer_watchdog_seconds": plan["verifier_watchdog_seconds"]
                }
            },
            image_ref=plan["verifier_image_ref"],
        )
        self.store = pilot.VerifierEvidenceStore(
            output_directory / "verifier-evidence.sqlite3",
            verification_key=self.signer.public_key,
            expected_signing_key_id=self.signer.signing_key_id,
            expected_identity=self.launcher.identity,
        )
        self.supervisor = pilot.VerifierSupervisor(
            self.launcher, self.signer, self.store
        )

    def public_evidence(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "image_ref": self.launcher.image_ref,
            "signing_key_id": self.signer.signing_key_id,
            "public_key_encoding": "RAW_ED25519_BASE64",
            "public_key_b64": base64.b64encode(self.signer.public_key).decode("ascii"),
            "public_key_sha256": pilot.sha256(self.signer.public_key),
        }

    def verify(
        self,
        source: pilot.FrozenLeanProblemSource,
        raw: bytes,
        *,
        case: str,
        mode: str,
        attempt: int,
        prompt: bytes,
        admitted_products: tuple[bytes, ...] = (),
    ) -> dict[str, Any]:
        if mode == "baseline":
            candidate, rule = pilot.adapt_model_completion(
                raw, theorem_name=source.native_id, trusted_source=source
            )
            arm = pilot.Arm.ORDINARY
        elif mode == "product":
            candidate, rule = pilot.adapt_product_completion(raw)
            arm = pilot.Arm.VERIFIED_CHAIN
        else:
            raise ValueError("unknown repair response mode")
        row: dict[str, Any] = {
            "case": case,
            "source_sha256": source.source_sha256,
            "problem_id": source.native_id,
            "attempt": attempt,
            "arm": arm.value,
            "adaptation_rule": rule,
            "raw_completion_sha256": pilot.sha256(raw),
            "raw_completion_bytes": len(raw),
            "raw_completion_utf8": raw.decode("utf-8"),
            "candidate_utf8": candidate.decode("utf-8"),
            "candidate_sha256": pilot.sha256(candidate),
            "candidate_bytes": len(candidate),
            "prompt_sha256": pilot.sha256(prompt),
            "prompt_utf8": prompt.decode("utf-8"),
            "admitted_product_sha256": [
                pilot.sha256(product) for product in admitted_products
            ],
            "classification_error": None,
            "verifier_evidence": None,
            "product_admitted": False,
            "final_solved": False,
        }
        subject = None
        try:
            classified = (
                pilot.classify_baseline_response(candidate, source)
                if mode == "baseline"
                else pilot.classify_product_response(candidate, source, attempt=attempt)
            )
            row["response_kind"] = classified.kind.value
            if classified.kind is pilot.ConfirmatoryResponseKind.NO_ANSWER:
                return row
            subject = pilot.build_verification_subject(
                source, classified, admitted_products=admitted_products
            )
        except ValueError as exc:
            row["response_kind"] = "MALFORMED"
            row["classification_error"] = str(exc)
        # Malformed answered bytes still reach the real verifier as a target
        # proof body, producing signed failure evidence rather than mock success.
        problem = pilot.BenchmarkProblemIdentity(
            "supernova-non-credit-repair-" + case, "v1", "validation", source.native_id
        )
        request = pilot.frozen_request(
            source=source,
            problem=problem,
            arm=arm,
            request_utf8=prompt,
            plan_sha256=self.plan_sha256,
            run_id=self.run_id,
            experiment_id="goal1-non-credit-repair",
            attempt=attempt,
            benchmark_root_sha256=pilot.sha256(b"NON_CREDIT_REPAIR_SOURCES"),
        )
        entry = pilot.DispatchEntry.create(
            run_id=request.run_id,
            sequence=self.sequence,
            problem_id=request.problem_id,
            arm=arm,
            attempt_index=attempt,
            request_sha256=request.frozen_request_sha256,
            completion_verifier_sha256=pilot.sha256(b"non-credit-repair-completion"),
            predecessor_sha256="0" * 64,
        )
        self.sequence += 1
        dispatch = pilot.BaselineDispatch(
            request=request, entry=entry, expected_events=()
        )

        def subject_builder(_dispatch: Any, exact_candidate: bytes, exact_source: Any):
            _require(
                exact_candidate == candidate and exact_source == source,
                "subject bytes drifted",
            )
            return subject

        port = pilot.ProductionVerifierPort(
            self.supervisor,
            {request.problem_id: source},
            run_spec_id=self.plan_sha256,
            execution_authority_sha256=pilot.sha256(b"non-credit-repair-authority"),
            protocol_rules_sha256=pilot.sha256(b"non-credit-repair-rules"),
            confirmatory_manifest_sha256=self.plan_sha256,
            subject_builder=subject_builder if subject is not None else None,
        )
        started = time.monotonic_ns()
        verification = port.verify(dispatch, candidate)
        row["verifier_wall_milliseconds"] = (time.monotonic_ns() - started) // 1_000_000
        row["dispatch_id"] = entry.dispatch_id
        row["verifier_status"] = verification.result.status.value
        row["verifier_evidence"] = {
            "record_sha256": verification.record.record_sha256,
            "signed_record": {
                "body": verification.record.body,
                "signature": verification.record.signature_b64,
            },
            "product_parser_admissible": verification.product_parser_admissible,
            "stdout_utf8": verification.blobs.stdout[:4096].decode("utf-8", "replace"),
            "stderr_utf8": verification.blobs.stderr[:4096].decode("utf-8", "replace"),
        }
        if row["response_kind"] == "PRODUCT_CANDIDATE":
            row["product_admitted"] = pilot.product_admission_decision(
                pilot.ProductChainArm.VERIFIED_CHAIN,
                pilot.ConfirmatoryResponseKind.PRODUCT_CANDIDATE,
                verification.result.status,
                syntax_admissible=verification.product_parser_admissible is True,
            )
        elif row["response_kind"] == "FINAL_ANSWER":
            row["final_solved"] = pilot.final_solve_decision(
                pilot.ConfirmatoryResponseKind.FINAL_ANSWER, verification.result.status
            )
        return row


def _verdict(row: dict[str, Any]) -> str | None:
    evidence = row.get("verifier_evidence")
    return (
        None
        if evidence is None
        else evidence["signed_record"]["body"]["observations"]["verdict"]
    )


def run_fixture_gates(verifier: RealVerifier, report: dict[str, Any]) -> None:
    source = synthetic_source()
    contract = pilot.load_object(pilot.PRODUCT_CONTROLS_PATH)
    rows = report["fixture_attempts"]
    gates = report["fixture_gates"]
    raw = b"```lean4\n" + source.source + b"  rfl\n```\n"
    rows.append(
        verifier.verify(
            source,
            raw,
            case="fixture-valid",
            mode="baseline",
            attempt=0,
            prompt=source.source,
        )
    )
    _require(
        rows[-1]["candidate_utf8"].encode() == b"  rfl\n"
        and _verdict(rows[-1]) == "VALID",
        "known-valid fenced fixture did not pass actual adapter and verifier",
    )
    gates["known_valid_fenced"] = "PASS"
    rows.append(
        verifier.verify(
            source,
            b"This is prose, not a Lean tactic.\n",
            case="fixture-prose",
            mode="baseline",
            attempt=1,
            prompt=source.source,
        )
    )
    _require(
        _verdict(rows[-1]) == "INVALID", "prose fixture did not produce signed INVALID"
    )
    gates["prose_invalid"] = "PASS"
    name = product_declaration_name(source, 0)
    product = (
        pilot.PRODUCT_PREFIX
        + f"lemma {name} : forall n : Nat, n = n := by\n  intro n\n  rfl\n".encode()
    )
    first = build_pilot_product_prompt(
        contract, source, attempt=0, admitted_products=()
    )
    rows.append(
        verifier.verify(
            source,
            product,
            case="fixture-product",
            mode="product",
            attempt=0,
            prompt=first.prompt_utf8,
        )
    )
    _require(
        rows[-1]["product_admitted"] and _verdict(rows[-1]) == "VALID",
        "synthetic product failed signed parser/verifier admission",
    )
    gates["product_parser_and_verifier"] = "PASS"
    admitted = rows[-1]["candidate_utf8"].encode("utf-8")
    final = build_pilot_product_prompt(
        contract, source, attempt=1, admitted_products=(admitted,)
    )
    _require(
        admitted == product and final.frozen_prompt_utf8.count(admitted) == 1,
        "admitted product is not exposed byte-exactly once",
    )
    gates["byte_exact_product_exposure"] = "PASS"
    final_candidate = pilot.FINAL_PREFIX + f"  exact {name} n\n".encode()
    rows.append(
        verifier.verify(
            source,
            final_candidate,
            case="fixture-final",
            mode="product",
            attempt=1,
            prompt=final.prompt_utf8,
            admitted_products=(admitted,),
        )
    )
    _require(
        rows[-1]["final_solved"] and _verdict(rows[-1]) == "VALID",
        "final fixture explicitly using product did not pass",
    )
    gates["final_explicit_product_use_valid"] = "PASS"
    report["fixture_transfer"] = {
        "status": "PASS",
        "classification": "FIXTURE_ONLY_MECHANISM_CHECK",
        "product_name": name,
        "product_response_sha256": pilot.sha256(admitted),
        "final_prompt": final.provenance(),
        "final_candidate_sha256": pilot.sha256(final_candidate),
        "admitted_response_byte_exact_exposure": True,
        "final_exact_qualified_product_application": True,
        "kernel_dependency_analysis": "NOT_PERFORMED",
        "model_capability_claim": "NONE",
    }


def _canary_prompt(
    source: Any, attempt: int, admitted: tuple[bytes, ...]
) -> tuple[bytes, dict[str, Any]]:
    built = build_pilot_product_prompt(
        pilot.load_object(pilot.PRODUCT_CONTROLS_PATH),
        source,
        attempt=attempt,
        admitted_products=admitted,
    )
    name = product_declaration_name(source, 0)
    instruction = (
        f"Forced diagnostic call 1 of at most 2: emit PRODUCT_CANDIDATE with one lemma named {name}, proving forall n : Nat, n = n.\n"
        if attempt == 0
        else f"Forced diagnostic call 2 of at most 2: emit FINAL_ANSWER. Prove the frozen theorem using the admitted qualified product {name} applied to n.\n"
    )
    prompt = (
        built.prompt_utf8
        + b"\nNON_CREDIT_FORCED_DIAGNOSTIC_SEQUENCE\n"
        + instruction.encode()
    )
    if admitted:
        _require(
            built.frozen_prompt_utf8.count(admitted[0]) == 1,
            "canary final prompt lost exact admitted product bytes",
        )
    provenance = built.provenance()
    provenance["forced_diagnostic_prompt_sha256"] = pilot.sha256(prompt)
    return prompt, provenance


def run_canary(
    verifier: RealVerifier,
    report: dict[str, Any],
    *,
    executor_image: str,
    checkpoint: Callable[[], None] = lambda: None,
) -> None:
    _require(
        report["archive_integrity"]["status"] == "PASS",
        "archive integrity gate not passed",
    )
    _require(
        all(report["fixture_gates"].get(key) == "PASS" for key in FIXTURE_GATES),
        "fixture gates not all passed",
    )
    _require(
        report["review_gate"]["status"] == "PASS",
        "current-code tests/review gate not passed",
    )
    source = synthetic_source()
    admitted: tuple[bytes, ...] = ()
    for attempt in range(MAX_MODEL_CALLS):
        if attempt == 1 and not admitted:
            report["canary_status"] = "STOPPED_NO_ADMITTED_PRODUCT"
            return
        _require(
            report["new_model_calls"] < MAX_MODEL_CALLS,
            "repair model-call budget exhausted",
        )
        prompt, provenance = _canary_prompt(source, attempt, admitted)
        report["new_model_calls"] += (
            1  # A failed container attempt also consumes a call.
        )
        row: dict[str, Any] = {
            "attempt": attempt,
            "prompt_utf8": prompt.decode(),
            "prompt_provenance": provenance,
        }
        report["canary_attempts"].append(row)
        checkpoint()
        try:
            observation = pilot.run_model_container(
                executor_image,
                prompt,
                theorem_name=source.native_id,
                response_mode="product",
                trusted_source=source,
            )
            row.update(
                {
                    "model_elapsed_milliseconds": observation.elapsed_milliseconds,
                    "model_image_id": observation.image_id,
                    "model_stderr": observation.stderr,
                    "teardown_observed": observation.teardown_observed,
                    "raw_completion_utf8": observation.raw_completion.decode("utf-8"),
                    "raw_completion_sha256": pilot.sha256(observation.raw_completion),
                    "raw_completion_bytes": len(observation.raw_completion),
                    "candidate_utf8": observation.completion.decode("utf-8"),
                    "candidate_sha256": pilot.sha256(observation.completion),
                    "candidate_bytes": len(observation.completion),
                    "adaptation_rule": observation.adaptation_rule,
                }
            )
            checkpoint()
            _require(
                observation.teardown_observed is True, "model teardown not observed"
            )
            verified = verifier.verify(
                source,
                observation.raw_completion,
                case=f"canary-{attempt}",
                mode="product",
                attempt=attempt,
                prompt=prompt,
                admitted_products=admitted,
            )
            row.update(verified)
            _require(
                row["candidate_utf8"].encode() == observation.completion,
                "model wrapper and verifier adapter disagree",
            )
        except Exception as exc:
            row["error"] = _error(exc)
            report["canary_status"] = "STOPPED_MODEL_OR_VERIFIER_ERROR"
            checkpoint()
            raise
        checkpoint()
        if attempt == 0:
            if row["product_admitted"]:
                admitted = (row["candidate_utf8"].encode(),)
            else:
                report["canary_status"] = "STOPPED_NO_ADMITTED_PRODUCT"
                return
        else:
            mention = product_declaration_name(source, 0) in row["candidate_utf8"]
            row["product_name_lexical_mention"] = mention
            row["kernel_dependency_analysis"] = "NOT_PERFORMED"
            report["canary_status"] = (
                "VALID_FINAL_WITH_LEXICAL_PRODUCT_MENTION"
                if row["final_solved"] and mention
                else "FINAL_DID_NOT_MEET_DIAGNOSTIC_CONDITION"
            )


def run_repair(
    *,
    archive: Path,
    output_directory: Path,
    executor_image: str | None = None,
    review_evidence: Path | None = None,
) -> dict[str, Any]:
    output_directory = output_directory.resolve()
    output_directory.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {
        "schema": "supernova.goal1.non-credit-repair-report.v1",
        "classification": "NON_CREDIT_ENGINEERING_DIAGNOSTIC",
        "scientific_credit": "NONE",
        "countable_attempts": 0,
        "execution_status": "RUNNING",
        "new_model_calls": 0,
        "max_new_model_calls": MAX_MODEL_CALLS,
        "archive_integrity": {"status": "NOT_RUN"},
        "archive_replays": [],
        "fixture_attempts": [],
        "fixture_gates": {},
        "review_gate": {"status": "NOT_REQUESTED"},
        "canary_attempts": [],
        "canary_status": "NOT_REQUESTED",
        "next_action": "STOP_AND_ANALYZE_BEFORE_ANY_LARGER_PILOT",
        "historical_outcomes_are_success_gate": False,
    }
    report_path = output_directory / "repair-report.json"

    def save() -> None:
        report_path.write_bytes(pilot.canonical_bytes(report) + b"\n")

    save()
    stage = "plan_integrity"
    try:
        plan = pilot.load_object(REPAIR_PLAN_PATH)
        plan_sha = pilot.sha256(REPAIR_PLAN_PATH.read_bytes())
        _require(
            plan["max_new_model_calls"] == MAX_MODEL_CALLS
            and plan["scientific_credit"] == "NONE"
            and plan["countable_attempts"] == 0,
            "repair plan bounds or credit changed",
        )
        _require(
            plan["canary"]["schedule"] == ["PRODUCT_CANDIDATE", "FINAL_ANSWER"]
            and plan["canary"]["skip_final_unless_product_admitted"] is True,
            "repair canary schedule changed",
        )
        report["plan_sha256"] = plan_sha
        report["source_sha256"] = current_code_digests()
        stage = "archive_integrity"
        responses, report["archive_integrity"] = load_archive(
            archive, plan=plan, input_directory=output_directory / "archive-input"
        )
        save()
        stage = "verifier_setup"
        verifier = RealVerifier(output_directory, plan, plan_sha)
        report["verifier"] = verifier.public_evidence()
        save()
        stage = "archive_replay"
        for index, response in enumerate(responses):
            row = {
                "original_record_sha256": response.original_record_sha256,
                "original_dispatch_id": response.original_dispatch_id,
                "original_candidate_sha256": pilot.sha256(response.original_candidate),
                "original_verdict": response.original_verdict,
            }
            report["archive_replays"].append(row)
            row.update(
                verifier.verify(
                    response.source,
                    response.raw_completion,
                    case=f"archive-{index}",
                    mode="baseline",
                    attempt=response.attempt,
                    prompt=response.source.source,
                )
            )
            row["representation_only_replay"] = True
            save()
        stage = "fixture_gates"
        run_fixture_gates(verifier, report)
        save()
        if executor_image is not None:
            stage = "review_gate"
            _require(
                review_evidence is not None,
                "--executor-image requires current-code --review-evidence",
            )
            report["review_gate"] = validate_review_evidence(review_evidence)
            save()
            stage = "canary"
            run_canary(verifier, report, executor_image=executor_image, checkpoint=save)
        report["execution_status"] = "COMPLETE"
    except Exception as exc:  # noqa: BLE001 - persist every typed terminal failure.
        report["execution_status"] = "STOPPED_WITH_ERROR"
        report["error"] = {"stage": stage, **_error(exc)}
    finally:
        save()
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--executor-image")
    parser.add_argument("--review-evidence", type=Path)
    args = parser.parse_args(argv)
    report = run_repair(
        archive=args.archive,
        output_directory=args.output_directory,
        executor_image=args.executor_image,
        review_evidence=args.review_evidence,
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "schema",
                    "scientific_credit",
                    "countable_attempts",
                    "execution_status",
                    "new_model_calls",
                    "canary_status",
                    "next_action",
                )
            },
            sort_keys=True,
        )
    )
    return 0 if report["execution_status"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
