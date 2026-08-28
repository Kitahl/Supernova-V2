# Integration checks

## G1-014 assembled dry cohort

Run:

```text
python integration/run_dry_cohort.py
python -m unittest discover -s integration -p "test_*.py"
```

This is a **synthetic DRY_RUN integration check**, not a benchmark run. It connects the
landed Goal-1 modules in one process:

1. seeded paired assignment and evaluator blinding;
2. request/result validation for ordinary, portfolio, product-only, and multi-fidelity;
3. a two-step verified chain using a deterministic schema verifier;
4. independently authenticated product admission;
5. five-arm complete-cost report closure using explicitly synthetic telemetry;
6. derivation of ten dry `OutcomeRecord` mappings; and
7. the deterministic evaluator, which must return `BLOCKED`.

The runner refuses `cost_model_frozen=true` and any phase other than `DRY_RUN`, so its
synthetic events cannot be reused as confirmatory evidence. Its JSON report names both
the scientific blockers and the still-unclosed schema seams. In particular, core
`OutcomeRecord` does not identify the `CompleteCostReport` from which cost was copied,
and the arm contracts do not carry a common problem/prompt digest. Those findings are
reported rather than silently papered over by the integration layer.

## G1-114 deterministic non-credit pilot

Run with Python 3.12 from the repository root:

```bash
python -m unittest discover -s integration -p "test_non_credit_pilot.py"
python -m integration.non_credit_pilot
```

This local engineering cohort exercises the five Goal 1 executor arms,
append-only dispatch and completion closure, verifier-receipt plumbing,
verified-chain retry/admission evidence, and complete-cost aggregation.

It makes no model, provider, network, Lean, or other verifier-process calls.
Its callbacks are fixed in-process engineering stubs. The benchmark name,
version, and root are copied from `goal1/BENCHMARK.lock.json`, but the problem ID,
problem bytes, problem digest, runtime digest, budget, and verifier receipts are
explicit engineering fixtures. The report therefore labels benchmark membership
as unestablished, emits `NON_CREDIT_PILOT`, emits no Goal 1 evaluation, and cannot
support a scientific PASS.

The pilot deliberately reports two unresolved integration seams:

1. The scientific evaluator consumes mapping-shaped outcome records and has no
   typed admission path for the dispatch `CompletionJoin`, signed completions,
   Lean receipts, verified-chain host ledger, or `CompleteCostReport`. This pilot
   does not translate authority evidence into unbound solved booleans.
2. Product-only retry provenance is an earlier attempt number. Verified-chain
   retry provenance instead carries a signed predecessor completion and must be
   present in both dispatch authority and the trusted execution ledger.

Request/response bytes, classifications, statuses, and event counts are fixed.
Completion signers are freshly generated, so dispatch/manifest/close and
host-ledger evidence IDs are intentionally fresh authority evidence. Executor
orchestration milliseconds also come from live monotonic timing. Those fields
are asserted structurally, while only the logical projection is compared across
runs.
