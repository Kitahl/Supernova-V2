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

Pending isolated remote run. Stop and inspect its signed evidence before any
model call, pilot, larger calibration or scientific authority change.
