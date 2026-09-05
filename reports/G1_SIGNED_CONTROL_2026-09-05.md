# G1V2-verifier-control handoff

Scientific status: NOT EVALUATED; zero countable attempts. No model calls.

## Scope

The runner verifies exactly the synthetic Mathlib Nat-reflexivity source used
in the local readiness failure. It calls VerifierSupervisor directly, without
importing any pilot/model module. This bypasses the model adapter deliberately:
it is a supervisor/host control, not an adapter qualification or an experiment.

The immutable image is 6e4008a00beebf5795e3afdc1affcfd549310d357a6662084450a534309b10ba.
Sandbox policy is unchanged from the local replay: 60 seconds per phase,
2 CPUs, 4 GiB, UID 10001, read-only and network-none. The publication JSON's
older image reference is explicitly overridden only for this diagnostic.
Reused publication configuration is hashed; it does not attest the newer image.
The actual observed image and phase timings are in the signed observations.
Internal import/compile timings are not exposed by the unmodified image.

The host creates an ephemeral signing key, retains only its public key, verifies
the returned signature and exact binding, and reads the SQLite record and blobs
back before reporting readiness. Only signed VALID yields exit 0. UNKNOWN,
INVALID and infrastructure errors yield exit 1 and a retained report. An existing
output directory is rejected. Historical signed records remain untouched.

## Remote execution isolation

Dedicated push branch: work/PM/G1V2-verifier-control. New workflow has only that
push trigger, contents/packages read permission, one 15-minute-bounded job, a
pull of the existing digest, one control invocation, and always-on evidence upload.
No build, publication, model call, benchmark preparation or recurring trigger.
Registry credentials are logged out before the verifier starts; checkout does
not persist git credentials. No key or database is mounted into either sandbox.

PR base: work/PM/G1V2-core-repairs. Check the exact three-dot diff before opening:
no integration/goal1_validation_pilot or runtime paths may enter this PR, because
those would trigger the historical executor build. General unit-test CI on the
PR is expected. No merge to main is part of this diagnostic.

## Local checks

- python -m ruff check scripts/run_verifier_control.py tests/test_verifier_control.py: passed.
- python -m ruff format --check scripts/run_verifier_control.py tests/test_verifier_control.py: passed.
- python -m unittest tests.test_verifier_control -v: 7 tests passed, repeated in isolation.
- python scripts/run_verifier_control.py --help: actual CLI entry point passed.
- YAML parsed with BaseLoader: push only, read-only permissions, one job.
- Eight self-contained VerifierEvidenceSecurityTests passed, including running
  container exits, both-phase success/rejection/UNKNOWN, heartbeat exhaustion,
  teardown failure and signed product admission. Only the unrelated heavy
  class-level cohort setup/teardown was bypassed; per-test fixtures ran.

Mock tests establish wiring and conservative outcomes, not real readiness.
Independent final review and the real remote result must be recorded below.

## Result

Local implementation commit: 411ac61 on work/PM/G1V2-verifier-control.
Its exact seven-file diff against 9a4d7b75d84050f48a47adfe42d47b7888364ed8
contains no integration/pilot or runtime paths. Other uncommitted repairs remain
preserved and are not included in this commit.

Independent review passed seven runner tests in 0.204 seconds and an additional
offline integration probe through the actual runner, supervisor, signer and
SQLite store with only Docker/security observations mocked. No actionable
finding. The review does not establish real container or Lean success.

The user explicitly approved publication. Commit 411ac6189f9d0a78fdfa76d6b71b69b8699cbc84
was pushed and its exact remote SHA read back. The isolated control ran once:
https://github.com/Kitahl/Supernova-V2/actions/runs/33946255412
Job 101252775765 succeeded. Draft PR 97 is stacked against core-repairs, unmerged:
https://github.com/Kitahl/Supernova-V2/pull/97

Observed signed verdict: VALID / ACCEPTED. Verifier elapsed 27122 ms;
elaboration 13962 ms, checking 13119 ms. Both separate keyless containers were
observed exited with exit code 0 and teardown confirmed. The image and 60-second
per-phase policy were unchanged. Pull time (~95 seconds) is not verifier time.

Artifact 9963442368 (581074 ZIP bytes), SHA256:
0d203a09016811a8d3ddde5428e4a4bbacf5964ae3307bfc443ff921252ab103.
Record SHA256:
fbc402f2a4d3e6bc3ccff85b94e472815dcf481ec73f84e297688f9377152088.
Independent local readback verified Ed25519 signature, exact input and run
binding, committed source hashes, one SQLite record, and all six stored blobs.
No model calls; no countable attempts. This establishes the exact control on
Actions, not prover capability or a scientific result. The local host differed;
the specific cause (paging, storage, or another host factor) remains unproven.

General PR CI run 33946354453 ran 600 tests: three failures, two skips. All three
failures were the newly added PM ticket violating the legacy 15-role board
invariant. The correction separates direct_work_tickets from historical tickets;
the scheduler mapping and all its existing assertions are preserved. No merge
until corrected GitHub CI passes. Documentation/board-only pushes cannot retrigger
the image control because they do not match its path filter.

Next: the existing bounded zero-model archive replay and product-transfer fixture
gates on Actions, under the same image. No image rebuild or larger model pilot.
