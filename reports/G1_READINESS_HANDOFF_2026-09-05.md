# Goal 1 readiness handoff - 2026-09-05

Scientific result: NOT EVALUATED. Countable attempts: 0. New model calls this
work session: 0. Do not repeat the successful signed reflexivity control.

## Completed and independently checked

- Control commit 411ac6189f9d0a78fdfa76d6b71b69b8699cbc84 ran on Actions once.
- Run https://github.com/Kitahl/Supernova-V2/actions/runs/33946255412 succeeded.
- The exact Mathlib reflexivity theorem received signed VALID / ACCEPTED.
- Verifier elapsed 27122 ms: elaboration 13962 ms, checking 13119 ms.
- Both isolated containers exited with code 0 and observed teardown.
- Signature, exact source/candidate/run/image binding, one SQLite record and
  six stored blob digests were independently verified after artifact download.
- Image unchanged: ghcr.io/kitahl/supernova-goal1-verifier@sha256:6e4008a00beebf5795e3afdc1affcfd549310d357a6662084450a534309b10ba.
- No image build, local Docker restart, model dispatch or scientific activation.
- PR https://github.com/Kitahl/Supernova-V2/pull/97 is draft and unmerged,
  based on work/PM/G1V2-core-repairs, not main.

Initial general CI found three bookkeeping failures among 600 tests (two skips):
the direct PM ticket was incorrectly put in the historical 15-task array.
Commit a8314b3bc14e15e23636636d6790605c462b65ae separates direct_work_tickets
without changing the scheduler mapping or deleting its assertions. All eight
orchestration tests and seven control tests pass locally. Full GitHub CI rerun
33946865429 is pending at this handoff's first write; read live before claiming
it passed. No control rerun was triggered by the board/documentation-only push.

Existing repair acceptance tests were rerun with:

```
python -m unittest integration.goal1_validation_pilot.test_validation_pilot integration.goal1_validation_pilot.test_model_container integration.goal1_validation_pilot.test_pilot_prompt integration.goal1_validation_pilot.test_repair -v
```

Result: 62 tests passed in 0.929 seconds. These are offline checks, not proof of
real product admission or model capability.

## Next bounded run - explicitly approved

The user approved this exact publication/job after the previous app-gate refusal:
"Yes. Proceed and finish goal 1." The control PR's full CI rerun 33946865429
has now completed successfully (unit tests, non-credit integration and evaluator).
The isolated readiness workflow and guard tests are created. Its release review,
local tests, publication and remote result are recorded below as they occur.

Reuse the implemented runner, without changing candidates or the runtime:

```
python integration/goal1_validation_pilot/run_repair.py --archive historical-pilot.zip --output-directory readiness-output
```

Omit --executor-image and --review-evidence. Default makes zero model calls.
Download only existing artifact 9927602076 from run 33843676746 in the same
repository. Before parsing, require ZIP SHA256:
2a087e76882a76240dbaa53ca28639dc5614a603aede04af10b72dd38092446f.

Run five immutable saved responses, then four fixed verifier requests covering:
fenced known-VALID input; prose INVALID; signed product parser/admission;
byte-exact product exposure; a final proof explicitly using that product.
Historical candidate success is not a gate and gains no retroactive credit.
Require all five fixture gates PASS and independently verify signed records.

Proposed Actions scope: one 25-minute job, exact push branch
work/PM/G1V2-readiness, contents/packages/actions read only, fixed digest pull,
logout before candidate execution, no persisted checkout credentials, no model,
build, image publication, recurring trigger, benchmark change or merge. Upload
only readiness-output as an artifact in Kitahl/Supernova-V2 (14-day retention).
Repository public identity was read back: id 1348165020, Kitahl/Supernova-V2.

Historical refusal (superseded by the explicit approval above): the app
auto-review gate rejected creation of this NEW workflow, stating that
the broad Goal 1 approval did not explicitly cover this artifact download,
GHCR authentication/Docker run and generated-evidence upload. The rejection
was not bypassed by another tool, ref or transport. No readiness workflow/test
file was created and no readiness job started at that time. The local branch
and existing repair edits were preserved.

Current publication checks: all 65 readiness tests pass; all 73 tests including
orchestration pass. Ruff on the runner/adapter/lifecycle/prompt and new guard test
passes. Actual CLI --help and YAML structure checks pass. Independent reviewer
reran the 65 tests, checked the exact staged 13 declared paths (no runtime/src or
dry-cohort edits), and found no execution/publication blocker. Remote readiness
results remain unobserved until the approved branch is pushed and the job ends.

Previously requested user approval (now granted): publish the bounded repair code/workflow to the existing
public Kitahl/Supernova-V2 repository and run this single zero-model Actions
readiness job, including the specified download, digest pull and evidence upload.

Before publishing, review the staged diff and workflow triggers. A PR touching
integration/goal1_validation_pilot currently triggers the old hermetic-executor
build workflow. Do not accidentally launch that build; keep this diagnostic
push-isolated and resolve PR scope deliberately. No merge without real CI and
local verification. After readiness, stop and inspect before new model work.

## Not yet established

Real adapter/product-transfer fixture success on Actions; corrected-model
product admission and reuse; current full-corpus compatibility and qualification;
validation calibration and prospective final protocol/complete-cost seal; a
countable final experiment and its registered PASS/FAIL analysis. The successful
control does not establish these. Local host-specific slowdown is demonstrated
by the comparison, but its precise resource cause remains unproven.
