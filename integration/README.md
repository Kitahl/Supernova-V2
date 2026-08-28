# G1-014 assembled dry cohort

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
