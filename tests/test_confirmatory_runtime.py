from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATH = ROOT / "goal1" / "CONFIRMATORY_RUNTIME.json"
SHA1 = re.compile(r"^[0-9a-f]{40}$")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON member: {key}")
        value[key] = item
    return value


def load_runtime(path: Path = RUNTIME_PATH) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_strict_object,
        parse_constant=lambda item: (_ for _ in ()).throw(
            ValueError(f"non-standard JSON constant: {item}")
        ),
    )
    if not isinstance(value, dict):
        raise ValueError("runtime contract must be an object")
    return value


def validate_runtime(runtime: dict[str, Any]) -> None:
    if runtime.get("schema_version") != 1:
        raise ValueError("runtime schema drift")
    if runtime.get("status") != "FROZEN":
        raise ValueError("runtime is not frozen")
    if runtime.get("runtime_id") != "goal1-confirmatory-lean-runtime-v1":
        raise ValueError("runtime identity drift")

    lean = runtime["lean"]
    mathlib = runtime["mathlib"]
    if lean["tag"] != "v4.33.1":
        raise ValueError("Lean version drift")
    if lean["toolchain"] != "leanprover/lean4:v4.33.1":
        raise ValueError("Lean toolchain drift")
    if lean["commit"] != "819816b2e0a3bf405af45ae5c7af2491d8f5bee6":
        raise ValueError("Lean commit drift")
    if mathlib["commit"] != "0df444a360eaa60ab8c11dca51a86af692955474":
        raise ValueError("Mathlib commit drift")
    if lean["version_probe"] != {
        "argv": ["lake", "env", "lean", "--short-version"],
        "expected_stdout_trimmed": "4.33.1",
    }:
        raise ValueError("Lean version probe drift")
    if lean["git_hash_probe"] != {
        "argv": ["lake", "env", "lean", "--githash"],
        "expected_stdout_trimmed": lean["commit"],
    }:
        raise ValueError("Lean git-hash probe drift")
    if lean["shell_source_evidence"] != {
        "ref": "v4.33.1",
        "path": "src/Lean/Shell.lean",
        "git_blob_sha": "3f73f6fe35d11cbccd9b6effe1bafad030fdbdce",
    }:
        raise ValueError("Lean CLI evidence drift")

    toolchain = mathlib["lean_toolchain"]
    if mathlib["tag"] != "v4.33.1":
        raise ValueError("Mathlib tag drift")
    if toolchain["exact_utf8"] != lean["toolchain"] + "\n":
        raise ValueError("Mathlib toolchain mismatch")
    if toolchain["git_blob_sha"] != "a8afa7d1b02d96f0671eba854a8dc4b416beb473":
        raise ValueError("toolchain blob drift")
    if (
        mathlib["lake_manifest"]["git_blob_sha"]
        != "1a4cd1dbe61cb8ca3779d972a6ffcd415ce50c52"
    ):
        raise ValueError("Lake manifest blob drift")

    materialization = runtime["materialization"]
    if materialization["expected_git_head"] != mathlib["commit"]:
        raise ValueError("Mathlib checkout mismatch")
    if materialization["verify_git_head_argv"] != ["git", "rev-parse", "HEAD"]:
        raise ValueError("checkout probe drift")
    if materialization["network_after_execution_seal"] != "DISABLED":
        raise ValueError("execution network is not disabled")
    if materialization["mutable_branch_or_tag_resolution_after_seal"] != "BLOCKED":
        raise ValueError("mutable ref resolution is not blocked")

    verifier = runtime["verifier"]
    if verifier["command_transport"] != "ARGV_WITHOUT_SHELL":
        raise ValueError("shell command transport is forbidden")
    expected_argv = [
        "lake",
        "env",
        "lean",
        "-t",
        "0",
        "-DwarningAsError=true",
        "-M",
        "4096",
        "{source_path}",
    ]
    if verifier["argv_template"] != expected_argv:
        raise ValueError("verifier argv drift")
    if verifier["working_directory"] != "EXACT_MATHLIB_CHECKOUT_ROOT":
        raise ValueError("verifier working-directory drift")
    path_rules = verifier["source_path_rules"]
    if (
        path_rules["must_be_absolute"] is not True
        or path_rules["must_be_regular_file"] is not True
        or path_rules["symlink"] != "BLOCKED"
        or path_rules["must_resolve_within"] != "SEALED_PER_CELL_WORK_DIRECTORY"
        or path_rules["bytes_sha256_must_match_dispatch"] is not True
    ):
        raise ValueError("source-path boundary drift")

    wrapper = verifier["theorem_wrapper_rules"]
    if wrapper["candidate_payload_kind"] != "TACTIC_BODY_ONLY":
        raise ValueError("candidate may mutate the theorem wrapper")
    if wrapper["benchmark_statement_bytes_must_match_frozen_record"] is not True:
        raise ValueError("benchmark statement is not byte-bound")
    if wrapper["theorem_name_must_equal_problem_id"] is not True:
        raise ValueError("theorem identity is not problem-bound")

    axiom = verifier["axiom_policy"]
    if axiom["allowed_exact"] != ["Classical.choice", "propext", "Quot.sound"]:
        raise ValueError("axiom allowlist drift")
    if axiom["forbidden_exact"] != ["sorryAx"]:
        raise ValueError("sorryAx rejection drift")
    if axiom["undeclared_axiom"] != "BLOCKED":
        raise ValueError("custom axioms are not blocked")
    if axiom["missing_or_unparseable_print_axioms"] != "BLOCKED":
        raise ValueError("missing axiom evidence is not blocked")

    passed = verifier["pass_rule"]
    if passed["process_exit_code"] != 0:
        raise ValueError("nonzero verifier exit became passable")
    for key in (
        "version_and_git_hash_probes_must_match",
        "stdout_and_stderr_must_be_complete",
        "warning_as_error_must_remain_enabled",
        "printed_axioms_must_be_subset_of_allowlist",
    ):
        if passed[key] is not True:
            raise ValueError(f"verifier pass rule weakened: {key}")

    limits = runtime["resource_limits"]
    if limits != {
        "lean_memory_megabytes": 4096,
        "host_wall_clock_milliseconds": 600000,
        "stdout_max_bytes": 1048576,
        "stderr_max_bytes": 1048576,
        "process_tree_kill_on_limit": True,
        "timeout_or_truncation_decision": "BLOCKED",
    }:
        raise ValueError("resource-limit drift")

    drift = runtime["drift_policy"]
    if not drift or any(value != "BLOCKED" for value in drift.values()):
        raise ValueError("runtime drift is not fail closed")


class ConfirmatoryRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runtime = load_runtime()

    def test_frozen_runtime_contract_is_valid(self) -> None:
        validate_runtime(self.runtime)

    def test_every_identity_and_invocation_drift_is_blocking(self) -> None:
        mutations = [
            ("lean.version", lambda x: x["lean"].__setitem__("tag", "v4.33.0")),
            (
                "lean.githash",
                lambda x: x["lean"]["git_hash_probe"].__setitem__(
                    "expected_stdout_trimmed", "0" * 40
                ),
            ),
            (
                "coordinated.lean.substitution",
                lambda x: (
                    x["lean"].__setitem__("commit", "0" * 40),
                    x["lean"]["git_hash_probe"].__setitem__(
                        "expected_stdout_trimmed", "0" * 40
                    ),
                ),
            ),
            (
                "mathlib.head",
                lambda x: x["materialization"].__setitem__(
                    "expected_git_head", "1" * 40
                ),
            ),
            (
                "coordinated.mathlib.substitution",
                lambda x: (
                    x["mathlib"].__setitem__("commit", "2" * 40),
                    x["materialization"].__setitem__(
                        "expected_git_head", "2" * 40
                    ),
                ),
            ),
            (
                "toolchain",
                lambda x: x["mathlib"]["lean_toolchain"].__setitem__(
                    "exact_utf8", "leanprover/lean4:nightly\n"
                ),
            ),
            (
                "lake.manifest",
                lambda x: x["mathlib"]["lake_manifest"].__setitem__(
                    "git_blob_sha", "not-a-blob"
                ),
            ),
            (
                "verifier.argv",
                lambda x: x["verifier"]["argv_template"].remove("-t"),
            ),
            (
                "source.boundary",
                lambda x: x["verifier"]["source_path_rules"].__setitem__(
                    "must_resolve_within", "ANYWHERE"
                ),
            ),
            (
                "network",
                lambda x: x["materialization"].__setitem__(
                    "network_after_execution_seal", "ENABLED"
                ),
            ),
            (
                "resource",
                lambda x: x["resource_limits"].__setitem__(
                    "host_wall_clock_milliseconds", 0
                ),
            ),
            (
                "axiom",
                lambda x: x["verifier"]["axiom_policy"]["allowed_exact"].append(
                    "user.custom"
                ),
            ),
        ]
        for label, mutate in mutations:
            with self.subTest(label=label):
                changed = copy.deepcopy(self.runtime)
                mutate(changed)
                with self.assertRaises(ValueError):
                    validate_runtime(changed)

    def test_sorry_and_custom_axioms_are_not_accepted(self) -> None:
        policy = self.runtime["verifier"]["axiom_policy"]
        allowed = set(policy["allowed_exact"])
        self.assertNotIn("sorryAx", allowed)
        self.assertFalse({"sorryAx", "user.custom"} <= allowed)
        self.assertEqual(policy["undeclared_axiom"], "BLOCKED")

    def test_duplicate_json_members_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate JSON member"):
            json.loads(
                '{"status":"FROZEN","status":"MUTATED"}',
                object_pairs_hook=_strict_object,
            )


if __name__ == "__main__":
    unittest.main()
