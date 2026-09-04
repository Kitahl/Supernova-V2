# Goal 1 v2 core repair gate — 2026-09-03

## Scientific state

- Goal 1: **NOT EVALUATED**
- Countable attempts: **0**
- Model dispatches in this repair: **0**
- Docker builds or pulls in this repair: **0**

## Authority transition

The v1 confirmatory protocol is explicitly superseded by
`goal1/GOAL1_V2_MIGRATION.json`. Both public v1 authority paths now reject that
protocol before they can create a run directory, read secret material, validate
the obsolete executor binding, or dispatch work.

The successor v2 experiment remains blocked. This checkpoint does not mint v2
execution authority and does not change the frozen scientific design.

## Completed engineering repairs

1. A read-only pre-dispatch conformance module now checks import provenance,
   sealed protocol-rule identity, every frozen authority's exact Git blob
   identity, the repository executor binding, and the existing execution
   authority in fail-closed order.
2. An unobserved container teardown is fatal. The verifier supervisor cannot
   issue a signature or append evidence after teardown observation fails.
3. The container response schema is v2. Lean heartbeat exhaustion is typed as
   `UNKNOWN / RESOURCE_LIMIT_HEARTBEAT`, ordinary rejected proofs remain
   `INVALID`, and an empty successful export is an infrastructure
   `UNKNOWN`.
4. A typed `UNKNOWN` is admitted only with the exact exit status 20. A
   contradictory response is recorded as malformed checker output.
5. Legacy durable-activation tests use an explicit fixture activation so they
   continue to test SQLite atomicity, replay rejection, concurrency, and
   readback without re-enabling v1 scientific authority.

## Verification

All commands ran on the frozen working tree after the final source edit.

```text
python -m pytest -o pythonpath=src +  tests/test_confirmatory_activation.py +  tests/test_confirmatory_execution_authority.py::ConfirmatoryExecutionAuthorityTests::test_fixed_v1_repository_authority_is_retired +  tests/test_confirmatory_run_retirement.py +  tests/test_evidence_bridge.py::VerifierEvidenceSecurityTests::test_real_bridge_blocks_authenticated_unknown_before_evaluator_projection -q

8 passed, 4 subtests passed
```

```text
python -m unittest discover -s tests -p "test_*.py"

Ran 583 tests
OK (skipped=6)
```

```text
python -m unittest discover -s integration -p "test_non_credit_pilot.py"

Ran 2 tests
OK
```

`git diff --check` passed. Git emitted only the checkout's existing Windows
LF-to-CRLF notices.

## Correct next order

1. Create and corpus-test benchmark v2 across both miniF2F splits (488
   statements) under the pinned verifier, with a finite frozen heartbeat budget.
   No model calls.
2. Freeze the exact challenge/export bytes and bind their digests into the
   verifier identity. This dependency follows benchmark compatibility because
   the expected export cannot be frozen before the normalized statement bytes
   exist.
3. Build and adversarially qualify the keyless verifier image: benign
   `VALID`, ordinary `INVALID`, typed resource `UNKNOWN`, hostile
   metaprogram execution proof, containment proof, and verified absence of
   Lake/Git/key material at runtime.
4. Establish one self-consistent v2 executor/model publication and make the
   full pre-dispatch conformance gate pass.
5. Recover the durable full-cohort runner, then run a two-attempt non-credit
   smoke.
6. Run locked validation-only calibration measuring ordinary best-of-k,
   product emission, product admission, admission timing, usable exposure, and
   downstream lift.
7. Determine paired sample size from calibration, prospectively seal v2, obtain
   independent review and CI, and only then consume the first countable nonce.

No later gate may be credited from this checkpoint.
