"""Offline boundaries only. Real Lean/signed gates are run by run_repair.py."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from integration.goal1_validation_pilot import run_repair as repair


def _row(raw: bytes, *, product: bool = False, final: bool = False) -> dict:
    return {
        "candidate_utf8": raw.decode(),
        "product_admitted": product,
        "final_solved": final,
        "verifier_evidence": {
            "signed_record": {"body": {"observations": {"verdict": "VALID"}}}
        },
    }


def _report() -> dict:
    return {
        "archive_integrity": {"status": "PASS"},
        "fixture_gates": {name: "PASS" for name in repair.FIXTURE_GATES},
        "review_gate": {"status": "PASS"},
        "new_model_calls": 0,
        "canary_attempts": [],
    }


class RepairTests(unittest.TestCase):
    def test_real_verifier_constructs_bound_request_before_container_dispatch(self):
        source = repair.synthetic_source()
        plan = repair.pilot.load_object(repair.REPAIR_PLAN_PATH)
        plan_sha = repair.pilot.sha256(repair.REPAIR_PLAN_PATH.read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            verifier = repair.RealVerifier(Path(directory).resolve(), plan, plan_sha)
            with patch.object(repair.pilot.ProductionVerifierPort, "verify") as port:
                port.side_effect = RuntimeError("container boundary reached")
                with self.assertRaisesRegex(RuntimeError, "container boundary reached"):
                    verifier.verify(source, b"  rfl\n", case="request-test",
                                    mode="baseline", attempt=0, prompt=source.source)
            dispatch, candidate = port.call_args.args
            request = dispatch.request
            self.assertIs(type(request), repair.pilot.FrozenProblemRequest)
            self.assertEqual(request.benchmark_root_sha256,
                             repair.pilot.sha256(b"NON_CREDIT_REPAIR_SOURCES"))
            self.assertEqual(request.problem_sha256, source.source_sha256)
            self.assertEqual(request.run_id, verifier.run_id)
            self.assertEqual(request.confirmatory_manifest_sha256, plan_sha)
            self.assertEqual(dispatch.entry.request_sha256, request.frozen_request_sha256)
            self.assertEqual(candidate, b"  rfl\n")
            self.assertEqual(type(request).from_mapping(request.to_mapping()), request)

    def test_relative_output_resolved_before_real_verifier_setup(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            output = Path(directory).relative_to(Path.cwd()) / "out"
            def setup(path, *args):
                self.assertTrue(path.is_absolute())
                raise RuntimeError("absolute path reached")
            with (
                patch.object(repair, "load_archive", return_value=([], {"status": "PASS"})),
                patch.object(repair, "RealVerifier", side_effect=setup),
            ):
                report = repair.run_repair(archive=Path("unused"), output_directory=output)
            self.assertEqual(report["error"]["message"], "absolute path reached")

    def test_archive_digest_rejected_before_zip_parser_or_output_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.zip"
            archive.write_bytes(b"hostile nonzip bytes")
            with patch.object(repair.zipfile, "ZipFile") as parsed:
                with self.assertRaisesRegex(repair.RepairGateError, "archive SHA256"):
                    repair.load_archive(
                        archive,
                        plan={"historical_artifact": {"zip_sha256": "0" * 64}},
                        input_directory=root / "copy",
                    )
                parsed.assert_not_called()
            self.assertFalse((root / "copy").exists())
            self.assertEqual(archive.read_bytes(), b"hostile nonzip bytes")

    def test_report_digest_rejected_before_database_open(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, "w") as archive:
                archive.writestr(repair.REPORT_MEMBER, b"untrusted report")
            path = root / "archive.zip"
            path.write_bytes(raw.getvalue())
            plan = {
                "historical_artifact": {
                    "zip_sha256": repair.pilot.sha256(raw.getvalue()),
                    "report_sha256": "0" * 64,
                }
            }
            with patch.object(repair.sqlite3, "connect") as connection:
                with self.assertRaisesRegex(repair.RepairGateError, "report SHA256"):
                    repair.load_archive(path, plan=plan, input_directory=root / "copy")
                connection.assert_not_called()

    def test_preserved_fenced_layout_builds_real_subject(self):
        source = repair.synthetic_source()
        body = b"  have h : n = n := by\n    rfl\n  exact h\n"
        raw = b"```lean4\n" + source.source + body + b"```\n"
        candidate, _ = repair.pilot.adapt_model_completion(
            raw, theorem_name=source.native_id, trusted_source=source
        )
        classified = repair.pilot.classify_baseline_response(candidate, source)
        subject = repair.pilot.build_verification_subject(source, classified)
        self.assertEqual(candidate, body)
        self.assertEqual(subject.candidate_source, body)
        self.assertEqual(subject.challenge_source, source.source)
        self.assertEqual(subject.theorem_names, (source.native_id,))

    def test_product_fixture_exact_admission_and_qualified_application(self):
        calls = []

        def verify(source, raw, **kwargs):
            calls.append((source, raw, kwargs))
            if kwargs["mode"] == "baseline":
                if kwargs["case"] == "fixture-prose":
                    row = _row(raw)
                    row["verifier_evidence"]["signed_record"]["body"]["observations"][
                        "verdict"
                    ] = "INVALID"
                    return row
                return _row(b"  rfl\n", final=True)
            return _row(
                raw, product=kwargs["attempt"] == 0, final=kwargs["attempt"] == 1
            )

        report = {"fixture_attempts": [], "fixture_gates": {}}
        repair.run_fixture_gates(SimpleNamespace(verify=verify), report)
        self.assertEqual(
            report["fixture_gates"], {name: "PASS" for name in repair.FIXTURE_GATES}
        )
        product_source, product, _ = calls[2]
        final_source, final, kwargs = calls[3]
        self.assertEqual(product_source, final_source)
        self.assertEqual(kwargs["admitted_products"], (product,))
        self.assertEqual(kwargs["prompt"].count(product), 1)
        name = repair.product_declaration_name(final_source, 0)
        self.assertEqual(
            final, repair.pilot.FINAL_PREFIX + f"  exact {name} n\n".encode()
        )
        classified = repair.pilot.classify_product_response(
            product, product_source, attempt=0
        )
        product_subject = repair.pilot.build_verification_subject(
            product_source, classified
        )
        self.assertEqual(product_subject.product_parser_expected_name, name)
        self.assertEqual(
            product_subject.product_parser_source,
            product[len(repair.pilot.PRODUCT_PREFIX) :],
        )
        final_classified = repair.pilot.classify_product_response(
            final, final_source, attempt=1
        )
        final_subject = repair.pilot.build_verification_subject(
            final_source, final_classified, admitted_products=(product,)
        )
        self.assertIn(product, final_subject.challenge_source)
        self.assertEqual(final_subject.candidate_source, final)
        self.assertEqual(report["fixture_transfer"]["model_capability_claim"], "NONE")

    def test_canary_requires_all_gates_before_any_model_call(self):
        for gate in ("archive_integrity", "review_gate"):
            report = _report()
            report[gate]["status"] = "FAIL"
            with patch.object(repair.pilot, "run_model_container") as model:
                with self.assertRaises(repair.RepairGateError):
                    repair.run_canary(None, report, executor_image="test")
                model.assert_not_called()
        for gate in repair.FIXTURE_GATES:
            report = _report()
            report["fixture_gates"].pop(gate)
            with patch.object(repair.pilot, "run_model_container") as model:
                with self.assertRaises(repair.RepairGateError):
                    repair.run_canary(None, report, executor_image="test")
                model.assert_not_called()

    def test_canary_budget_is_two_and_exact_product_reaches_final(self):
        report = _report()
        source = repair.synthetic_source()
        name = repair.product_declaration_name(source, 0)
        product = (
            repair.pilot.PRODUCT_PREFIX
            + f"lemma {name} : forall n : Nat, n = n := by\n  intro n\n  rfl\n".encode()
        )
        final = repair.pilot.FINAL_PREFIX + f"  exact {name} n\n".encode()
        observations = [
            repair.pilot.ModelContainerObservation(
                raw, raw, "EXACT_PRODUCT_PROTOCOL_RESPONSE", 1, "sha256:test", "", True
            )
            for raw in (product, final)
        ]
        calls = []

        def verify(_source, raw, **kwargs):
            calls.append(kwargs)
            return _row(
                raw, product=kwargs["attempt"] == 0, final=kwargs["attempt"] == 1
            )

        with patch.object(
            repair.pilot, "run_model_container", side_effect=observations
        ) as model:
            repair.run_canary(
                SimpleNamespace(verify=verify), report, executor_image="test"
            )
        self.assertEqual(model.call_count, 2)
        self.assertEqual(report["new_model_calls"], 2)
        self.assertEqual(calls[1]["admitted_products"], (product,))
        self.assertEqual(calls[1]["prompt"].count(product), 1)
        self.assertEqual(
            report["canary_status"], "VALID_FINAL_WITH_LEXICAL_PRODUCT_MENTION"
        )
        self.assertEqual(
            report["canary_attempts"][1]["kernel_dependency_analysis"], "NOT_PERFORMED"
        )
        with patch.object(repair.pilot, "run_model_container") as model:
            with self.assertRaisesRegex(repair.RepairGateError, "budget exhausted"):
                repair.run_canary(None, report, executor_image="test")
            model.assert_not_called()

    def test_unadmitted_product_stops_after_one_call(self):
        report = _report()
        raw = repair.pilot.NO_ANSWER
        observation = repair.pilot.ModelContainerObservation(
            raw, raw, "EXACT", 1, "sha256:test", "", True
        )
        with patch.object(
            repair.pilot, "run_model_container", return_value=observation
        ) as model:
            repair.run_canary(
                SimpleNamespace(verify=lambda *args, **kwargs: _row(raw)),
                report,
                executor_image="test",
            )
        self.assertEqual(model.call_count, 1)
        self.assertEqual(report["new_model_calls"], 1)
        self.assertEqual(report["canary_status"], "STOPPED_NO_ADMITTED_PRODUCT")

    def test_model_exception_retains_diagnostic_and_consumes_one_call(self):
        class TypedFailure(RuntimeError):
            diagnostic = SimpleNamespace(
                to_dict=lambda: {"failure_stage": "attach", "teardown_observed": True}
            )

        report = _report()
        with (
            patch.object(
                repair.pilot, "run_model_container", side_effect=TypedFailure("failed")
            ) as model,
            self.assertRaises(TypedFailure),
        ):
            repair.run_canary(None, report, executor_image="test")
        self.assertEqual(model.call_count, 1)
        self.assertEqual(report["new_model_calls"], 1)
        self.assertEqual(
            report["canary_attempts"][0]["error"]["model_container_diagnostic"][
                "failure_stage"
            ],
            "attach",
        )

    def test_verifier_exception_preserves_consumed_model_bytes_and_checkpoints(self):
        report = _report()
        raw = b"UNIQUE_MODEL_OUTPUT"
        observation = repair.pilot.ModelContainerObservation(
            raw, raw, "RAW", 1, "sha256:test", "", True
        )
        snapshots = []

        def verify(*args, **kwargs):
            self.assertIn("UNIQUE_MODEL_OUTPUT", snapshots[-1])
            raise RuntimeError("verifier unavailable")

        with (
            patch.object(repair.pilot, "run_model_container", return_value=observation),
            self.assertRaisesRegex(RuntimeError, "verifier unavailable"),
        ):
            repair.run_canary(
                SimpleNamespace(verify=verify),
                report,
                executor_image="test",
                checkpoint=lambda: snapshots.append(json.dumps(report)),
            )
        row = report["canary_attempts"][0]
        self.assertEqual(row["raw_completion_utf8"].encode(), raw)
        self.assertEqual(row["candidate_sha256"], repair.pilot.sha256(raw))
        self.assertEqual(report["new_model_calls"], 1)
        self.assertIn("verifier unavailable", snapshots[-1])

    def test_default_never_dispatches_model_and_output_must_be_fresh(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            verifier = SimpleNamespace(
                public_evidence=lambda: {"public_key_b64": "test-only"}
            )
            with (
                patch.object(
                    repair, "load_archive", return_value=([], {"status": "PASS"})
                ),
                patch.object(repair, "RealVerifier", return_value=verifier),
                patch.object(repair, "run_fixture_gates"),
                patch.object(repair.pilot, "run_model_container") as model,
            ):
                report = repair.run_repair(
                    archive=Path("unused"), output_directory=output
                )
                model.assert_not_called()
            self.assertEqual(report["new_model_calls"], 0)
            self.assertEqual(report["scientific_credit"], "NONE")
            self.assertEqual(report["countable_attempts"], 0)
            self.assertTrue((output / "repair-report.json").is_file())
            with self.assertRaises(FileExistsError):
                repair.run_repair(archive=Path("unused"), output_directory=output)

    def test_failure_report_saved_and_no_model_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            with (
                patch.object(
                    repair,
                    "load_archive",
                    side_effect=repair.RepairGateError("digest failure"),
                ),
                patch.object(repair.pilot, "run_model_container") as model,
            ):
                report = repair.run_repair(
                    archive=Path("unused"),
                    output_directory=output,
                    executor_image="test",
                )
                model.assert_not_called()
            saved = json.loads((output / "repair-report.json").read_text())
            self.assertEqual(saved, report)
            self.assertEqual(saved["error"]["stage"], "archive_integrity")
            self.assertEqual(saved["error"]["type"], "RepairGateError")

    def test_review_evidence_requires_exact_current_code_and_artifact_digests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests.txt").write_bytes(b"offline tests passed")
            (root / "review.txt").write_bytes(b"independent review passed")
            value = {
                "schema": "supernova.goal1.repair-review-evidence.v1",
                "scope": repair.REVIEW_SCOPE,
                "source_sha256": {"test.py": "a" * 64},
                "unit_tests": {
                    "status": "PASS",
                    "command": "unittest",
                    "evidence_path": "tests.txt",
                    "evidence_sha256": repair.pilot.sha256(b"offline tests passed"),
                },
                "independent_review": {
                    "status": "PASS",
                    "reviewer": "test-reviewer",
                    "evidence_path": "review.txt",
                    "evidence_sha256": repair.pilot.sha256(
                        b"independent review passed"
                    ),
                },
            }
            path = root / "review.json"
            path.write_text(json.dumps(value))
            with patch.object(
                repair, "current_code_digests", return_value={"test.py": "a" * 64}
            ):
                self.assertEqual(
                    repair.validate_review_evidence(path)["status"], "PASS"
                )
                (root / "review.txt").write_bytes(b"changed")
                with self.assertRaisesRegex(repair.RepairGateError, "artifact digest"):
                    repair.validate_review_evidence(path)
            with (
                patch.object(
                    repair, "current_code_digests", return_value={"test.py": "b" * 64}
                ),
                self.assertRaisesRegex(repair.RepairGateError, "current code"),
            ):
                repair.validate_review_evidence(path)


if __name__ == "__main__":
    unittest.main()
