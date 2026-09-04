# Goal 1 benchmark-v2 candidate gate — 2026-09-03

## State

- Goal 1: **NOT EVALUATED**
- Countable attempts: **0**
- Model calls in this gate: **0**
- Scientific dispatch: **BLOCKED**
- Candidate status: **BUILT_UNQUALIFIED**

## Proven inputs

The two public source artifacts were acquired at their repository-pinned
revisions and matched the frozen SHA-256 values:

- DeepSeek miniF2F JSONL:
  `d6654c88476ff14db19d57dea25bd955a240525fb52171e460f89d98f83d7dd5`
- Kimina corrected test Parquet:
  `a32985e3d44b5165fbd586f453055cd17de0d081f43d7806cbba3f321346a8c5`

The existing assembler reproduced 244 validation records and 244 test records.
The existing benchmark validator passed with frozen v1 root:

`914c05427e1e7e0979f4ca058f90fb3138ee0d3319233b415194c10e67d3683b`

## Exact inventory

- 488 of 488 records contain `set_option maxHeartbeats 0`.
- 33 validation and 31 test records contain legacy big-operator `in`
  syntax.
- Only `validation/amc12a_2003_p1` currently has direct runtime evidence
  proving that syntax incompatible with Lean 4.33.1.

## Candidate transformation

`goal1/BENCHMARK_V2_TRANSFORMS.json` is explicitly
`CANDIDATE_UNSEALED_NON_CREDIT`.

It performs:

1. 488 exact header replacements:
   `set_option maxHeartbeats 0` to
   `set_option maxHeartbeats 500000`.
2. One exact, evidence-backed statement patch for
   `validation/amc12a_2003_p1`:
   `∑ k in ...` to `∑ k ∈ ...` in its two sums.

The 500,000 value is a qualification candidate based on the LeanMarathon
prior. It is not frozen for scientific use until known-good proof
qualification passes.

The other 63 legacy statements remain byte-unchanged. They may be patched only
after the 488-statement gate produces direct failure evidence for them.

The deterministic candidate has:

- 244 validation records;
- 244 test records;
- 460,519 locked corpus bytes;
- root SHA-256
  `c404215b329dcaca4228a8a23eaa21b64277c85e536c441232e91355ec96d9d8`.

The standalone lock checker passed over exactly
`candidate/corpus/{validation.jsonl,test.jsonl}`.

## Compatibility gate

`scripts/check_benchmark_v2_compatibility.py`:

- independently verifies the candidate lock and transform binding;
- materializes each statement by exact suffix append of `sorry`;
- requires Lean 4.33.1;
- runs all 488 records with two workers;
- records every result plus mean, median, p95, minimum, and maximum latency;
- treats timeout/error/failure as blocking evidence, not mathematical
  invalidity.

`.github/workflows/goal1_benchmark_v2_compatibility.yml` is manual-only,
read-only, non-credit, and contains no model or secret step. It pins the
official checkout, Python setup, Lean, and artifact-upload actions to immutable
commits. The Lean action is pinned at
`38fbc41a8c28c4cbaec22d7f7de508ec2e7c0dd9`. It installs the Mathlib cache,
rebuilds the candidate from the public pinned sources, and runs the entire
compatibility gate.

## Verification

```text
python -m unittest discover -s tests -p "test_*.py"

Ran 590 tests
OK (skipped=6)
```

```text
python -m unittest discover -s integration -p "test_non_credit_pilot.py"

Ran 2 tests
OK
```

Workflow YAML parsing passed. Exact trigger audit found only
`workflow_dispatch`; no schedule, push, pull request, model, or secret
reference is present.

## Current blocker and next action

The local Docker Linux engine is unavailable:

```text
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine
```

No Docker image was pulled or built. The branch has not been published because
the environment's publication safety review rejected the push without an
explicit destination authorization.

The next correct action is:

1. push `work/PM/G1V2-core-repairs` to
   `https://github.com/Kitahl/Supernova-V2`;
2. manually run `Goal 1 benchmark-v2 compatibility`;
3. inspect the complete 488-record report;
4. add only failure-proven per-problem patches and repeat until the corpus gate
   passes;
5. then bind the qualified challenge/export digests into verifier identity.

No verifier publication, model smoke, calibration, protocol seal, or
countable attempt may precede that result.
