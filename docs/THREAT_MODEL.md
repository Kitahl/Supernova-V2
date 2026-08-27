# Goal 1 threat model — independent adversarial audit

Ticket: `G1-012` (`EXT01`)

This document is an adversarial design review, not an admission certificate. It separates
**EVIDENCE** (repository state or a primary/direct external source) from **INFERENCE** (the audit
conclusion drawn from that evidence). A threat remains open until the experiment has a prospective,
machine-checkable falsifier or mitigation.

## Target claim and causal estimand

**EVIDENCE — repository.** `goal1/GOAL1.json` asks whether, within one problem, a chain of
independently verified intermediate products solves more problems than ordinary, portfolio,
product-only, and multi-fidelity controls under the same frozen complete-cost budget. The current
experiment is explicitly `DRY_RUN`; the benchmark is `UNSELECTED`/`UNPINNED`/`UNLOCKED`, and
`cost_model_frozen=false`.

**INFERENCE.** A positive result supports a mechanism claim about *verify-before-consume* only if
treatment and controls differ by that mechanism rather than by decomposition, adaptive search,
state persistence, verifier information, retry/stopping policy, representation, model/tool access,
or actual compute. Otherwise the estimand is performance of a bundled agent architecture.

## Threat ledger

### T1 — Treatment-bundle confounding (`CRITICAL`)

**EVIDENCE — repository.** Proposed G1-006 / PR #5 implements a stateful
`propose -> verify -> consume -> propose` loop with verifier-conditioned transitions. Proposed
ordinary/portfolio contracts do not expose the same intermediate-product channel or cross-attempt
state.

**INFERENCE.** A treatment win could be caused by decomposition, adaptive continuation, persistent
state, verifier feedback, or composition rather than verified-product consumption itself.

**Required falsifier/mitigation.** Freeze controls/ablations that match model, tools, prompt budget,
decomposition opportunity, state capacity, retry policy, and search depth while removing one
mechanism at a time. Include verifier-matched non-chaining and chaining-without-gating controls.

### T2 — A common ceiling is not equal complete cost (`CRITICAL`)

**EVIDENCE — repository.** Goal 1 uses one five-dimensional budget ceiling. Proposed G1-007 checks
whether each arm stays under that ceiling but does not require equal realized resource vectors.
**EVIDENCE — external.** Snell et al. show that test-time compute allocation materially changes
reasoning performance: <https://arxiv.org/abs/2408.03314>.

**INFERENCE.** An arm can consume substantially more generator/verifier work than a control while
both remain legal. A compute advantage can therefore masquerade as a mechanism advantage.

**Required falsifier/mitigation.** Match realized cost vectors, allow controls to spend residual
budget under a frozen policy, or compare preregistered performance-versus-cost frontiers. Record
actual per-cell costs even when an arm stops early.

### T3 — Intermediate verifier access is a privileged search oracle (`CRITICAL`)

**EVIDENCE — external.** Cobbe et al. use a verifier to rank generated candidates and improve math
answer selection: <https://arxiv.org/abs/2110.14168>. Seed-Prover iteratively uses Lean feedback and
proved lemmas: <https://arxiv.org/abs/2507.23726>. **EVIDENCE — repository.** Proposed G1-006 exposes
PASS/FAIL transitions during search.

**INFERENCE.** Search-time verification supplies information, not merely assurance. If controls do
not receive matched query count, response vocabulary, and diagnostic bandwidth, the experiment does
not isolate downstream consumption.

**Required falsifier/mitigation.** Freeze verifier-query budgets and feedback semantics. Keep the
search-verifier channel distinct from the blind final outcome checker and include a verifier-reranked
portfolio/control when relevant.

### T4 — Distinct IDs do not establish independent verification (`HIGH`)

**EVIDENCE — repository.** Proposed G1-006 requires `verifier_id != producer_id`.

**INFERENCE.** Different strings do not prove independent processes, state, parsers, runtimes, or
trusted bases.

**Required falsifier/mitigation.** Freeze verifier implementation/version/hash, runtime, allowed
axioms/imports, timeout, and output contract. Replay final formal artifacts in a clean verifier
process that the treatment cannot modify.

### T5 — Benchmark contamination and benchmark-selection leakage (`CRITICAL` until pinned)

**EVIDENCE — repository.** The live Goal 1 bootstrap has no selected/locked benchmark.
**EVIDENCE — external.** LiveBench was designed around frequently refreshed questions to reduce
contamination risk: <https://arxiv.org/abs/2406.19314>. Deng et al. document contamination signals
in modern LLM benchmarks: <https://aclanthology.org/2024.naacl-long.482/>.

**INFERENCE.** Selecting or modifying confirmatory items after observing pilot behavior, or using a
public benchmark exposed during model training/tuning, can inflate apparent capability.

**Required falsifier/mitigation.** Freeze benchmark identity, version, problem IDs, split, hashes,
and model version before confirmatory treatment tuning. Prefer fresh/hidden or post-model-release
items and report residual contamination risk when training provenance is unavailable.

### T6 — Formal-library answer leakage (`HIGH`)

**EVIDENCE — external.** LeanDojo provides a reproducible Lean environment and a split intended to
test generalization to novel premises:
<https://proceedings.neurips.cc/paper_files/paper/2023/file/4441469427094f8873d0fecb0c4e1cee-Paper-Datasets_and_Benchmarks.pdf>.

**INFERENCE.** A held-out theorem can still leak through an exact/near-duplicate statement, known
proof, or decisive helper lemma in an allowed library or retrieval corpus.

**Required falsifier/mitigation.** Freeze the proof-library commit and retrieval corpus, audit
exact/near duplicates and known proofs, define legal helper lemmas, and apply identical retrieval
visibility to comparable arms.

### T7 — Cross-arm and cross-problem state leakage (`CRITICAL`)

**EVIDENCE — repository.** Proposed assignment work blinds evaluator-visible arm order, but the live
bootstrap does not yet freeze execution-isolation semantics.

**INFERENCE.** Conversation state, caches, retrieved lemmas, verifier diagnostics, or learned
products can leak between arms/problems even when scoring is blind.

**Required falsifier/mitigation.** Start every `(problem, arm, replicate)` from a fresh frozen base
context, randomize/counterbalance execution order, and prohibit cross-problem product reuse for Goal
1 unless transfer is explicitly part of a later estimand.

### T8 — Unequal model/tool/prompt capability (`CRITICAL`)

**EVIDENCE — repository.** Goal 1 does not yet freeze provider/model version, sampling policy,
reasoning-effort mode, prompt template, context-window policy, or external tool set.

**INFERENCE.** Arm-specific capability differences can dominate the intended mechanism effect.

**Required falsifier/mitigation.** Freeze model/provider/version, decoding/seed policy, reasoning
effort, prompts, context limits, network/tool access, and retry semantics across causal controls.

### T9 — Cost-accounting gaps and free work (`HIGH`)

**EVIDENCE — repository.** Proposed G1-007 identifies several exclusions and rejects silently
zero-filling missing telemetry.

**INFERENCE.** Hidden/reasoning tokens, cached tokens, failed requests, external tool compute,
retrieval/indexing, preprocessing, shared setup, and human intervention can still subsidize one arm.

**Required falsifier/mitigation.** Predeclare the accounting boundary; charge treatment-specific
setup/tool calls and all retries/failures; preserve cached/uncached semantics; fail closed on missing
telemetry; report cumulative resources and wall-clock latency separately.

### T10 — Early stopping and retry asymmetry (`HIGH`)

**EVIDENCE — external.** Test-time strategies can allocate compute adaptively by difficulty, changing
efficiency and accuracy: <https://arxiv.org/abs/2408.03314>.

**INFERENCE.** Informative retries for one arm and fixed attempts for another create an unfair search
advantage even under an equal maximum budget.

**Required falsifier/mitigation.** Pre-register success stopping, timeout, retry, and residual-budget
reuse rules. Controls should receive the strongest analogous adaptive allocation legal under their
mechanism definition.

### T11 — Verifier overfitting and diagnostic-channel exploitation (`HIGH`)

**EVIDENCE — external.** Prover Agent and Seed-Prover use Lean feedback iteratively:
<https://arxiv.org/abs/2506.19923> and <https://arxiv.org/abs/2507.23726>.

**INFERENCE.** A search process can overfit checker diagnostics or exploit an implementation bug.
Final PASS then establishes acceptance by that checker, not necessarily the broader intended
mathematical construct.

**Required falsifier/mitigation.** Narrow the search-verifier response contract where possible,
reject unsafe proof mechanisms, preserve raw verifier evidence, and independently replay final
artifacts in a clean environment.

### T12 — Stochastic seed luck and undefined replication (`HIGH`)

**EVIDENCE — repository.** Goal 1 reduces each problem/arm to a binary solved outcome but does not
yet freeze how multiple stochastic samples are reduced to that binary value.

**INFERENCE.** Selective reruns or favorable seeds create researcher degrees of freedom.

**Required falsifier/mitigation.** Freeze seeds/replicate counts and the per-problem aggregation rule
before confirmation. Charge every sample and never rerun only losing cells outside a symmetric,
deterministic retry rule.

### T13 — Confirmatory-set reuse and adaptive overfitting (`HIGH`)

**EVIDENCE — repository.** The system is actively being built while the final benchmark remains
unlocked.

**INFERENCE.** Repeatedly inspecting confirmatory failures and changing prompts, decomposition,
verifier interfaces, or budgets converts the holdout into development data.

**Required falsifier/mitigation.** Maintain separate development and confirmatory sets. Expose the
confirmatory set once under the frozen protocol or use a preregistered limited-retest rule followed
by a new untouched holdout after substantive changes.

### T14 — Dry-run evidence accidentally promoted to science (`BLOCKER if misreported`)

**EVIDENCE — repository.** The current experiment is `goal1-bootstrap-dry-run`, has two required dry
problem IDs, an unselected benchmark, and an unfrozen cost model.

**INFERENCE.** The dry run can test schemas, transport, failure handling, and evaluator behavior but
cannot support the scientific hypothesis.

**Required falsifier/mitigation.** Keep scientific evaluation `BLOCKED` until the benchmark/split and
complete-cost semantics are frozen. Never cite dry-run solve counts as mechanism evidence.

### T15 — Kernel-valid can still be benchmark-invalid (`CRITICAL` for formal benchmarks)

**EVIDENCE — external.** Ammanamanchi, Bhat, and Biderman report thousands of findings in Lean
benchmarks, including mechanically certified vacuity/counterexample/unsafe-axiom defects:
<https://arxiv.org/abs/2606.29493>.

**INFERENCE.** Kernel acceptance does not prove that the formal statement faithfully represents the
intended benchmark problem.

**Required falsifier/mitigation.** Run mechanical dataset checks for vacuity, unsafe axioms/imports,
contradictory assumptions, and degenerate formalizations; preserve an audited informal-to-formal
mapping and freeze corrected benchmark snapshots prospectively.

### T16 — Verifier computation and verification frequency are treatment variables (`CRITICAL`)

**EVIDENCE — external.** Setlur et al. show verifier-based and verifier-free test-time strategies can
scale differently: <https://proceedings.mlr.press/v267/setlur25a.html>. Singhi et al. analyze the
solve-versus-verify compute tradeoff: <https://arxiv.org/abs/2504.01005>. Chen et al. show
verification frequency can change accuracy and FLOP efficiency: <https://arxiv.org/abs/2505.11730>.

**INFERENCE.** Equal generator calls are not equal inference budgets when one arm receives more
verifier compute or more frequent information.

**Required falsifier/mitigation.** Log/cap verifier invocations, tokens/FLOPs or timing, diagnostics,
and granularity. Compare joint generator+verifier resources and include a verifier-frequency-matched
control when claiming a chaining effect.

### T17 — Public-benchmark success may be instance memorization rather than structural reasoning (`HIGH`)

**EVIDENCE — external.** MathArena reports contamination signals on older public competitions and
uses newly released problems: <https://arxiv.org/abs/2505.23281>. VAR-MATH reports large drops on
symbolically variabilized versions of common math problems: <https://arxiv.org/abs/2507.12885>.

**INFERENCE.** A formally correct answer can still arise from memorized instance/pattern retrieval.

**Required falsifier/mitigation.** Prefer fresh/hidden items and, where the benchmark permits,
predeclare structure-preserving variants as a robustness diagnostic with a frozen aggregation rule.

### T18 — Hosted-model time drift can masquerade as an arm effect (`CRITICAL` for API models)

**EVIDENCE — external.** Chen, Zaharia, and Zou measured material behavior changes between hosted
GPT versions over a short interval: <https://arxiv.org/abs/2307.09009>. Gemini documentation states
that `latest` aliases can move while specific stable names are intended to remain stable:
<https://ai.google.dev/gemini-api/docs/models>.

**INFERENCE.** If arm identity correlates with execution time, a backend/model rollover can appear as
a treatment effect.

**Required falsifier/mitigation.** Use specific stable versions where possible, record requested and
returned model identity plus trusted call time, run paired arms in randomized/counterbalanced narrow
time blocks, and fail closed or segment a run if model identity changes.

### T19 — "Deterministic" inference can vary with backend execution (`HIGH`)

**EVIDENCE — external.** Yuan et al. show batch size, GPU count/type, and numerical precision can
change outputs and benchmark accuracy under greedy decoding:
<https://proceedings.neurips.cc/paper_files/paper/2025/hash/f80094a824ba5912d4a2de169c404a40-Abstract-Conference.html>.
Ouyang et al. report run-to-run variation and that temperature zero does not guarantee deterministic
outputs: <https://arxiv.org/abs/2308.02828>.

**INFERENCE.** A frozen model name, prompt, seed, and temperature do not by themselves guarantee
reproducible paired outcomes.

**Required falsifier/mitigation.** Freeze self-hosted serving engine/hardware/precision/batching and
deterministic-kernel settings, or treat hosted backend execution as residual uncertainty with
randomized paired order and a prospective replication/sensitivity plan.

### T20 — Soft contamination can survive exact-match decontamination (`CRITICAL` for public pretrained benchmarks)

**EVIDENCE — external.** Spiesberger et al. show semantic duplicates can evade n-gram filtering and
report measurable benchmark gains from semantic-duplicate exposure:
<https://arxiv.org/abs/2602.12413>.

**INFERENCE.** Exact hash/string or n-gram absence cannot certify out-of-distribution reasoning.

**Required falsifier/mitigation.** Audit semantic/family-level overlap where training-corpus access
exists, prefer post-model-cutoff/fresh families, and explicitly state residual soft-contamination
risk when a clean certificate is impossible.

### T21 — Hosted hidden-reasoning telemetry can make complete-cost parity unobservable (`HIGH` for opaque APIs)

**EVIDENCE — external.** Sun et al. identify an auditability problem when commercial APIs charge for
hidden reasoning tokens whose traces are not exposed: <https://arxiv.org/abs/2505.13778>.

**INFERENCE.** Provider counters can support a provider-reported usage comparison without proving an
independently measured compute comparison.

**Required falsifier/mitigation.** Preserve raw usage metadata/request IDs and hidden-token counters;
never infer missing hidden usage from visible output or zero-fill it. Prefer auditable inference for
a confirmatory causal claim, or require the conclusion to survive a frozen sensitivity bound.

### T22 — Product-only alignment improved, but verifier-conditioned retry remains non-equivalent (`CRITICAL` for a pure gating claim)

**EVIDENCE — repository.** Current proposed G1-005 / PR #12 now matches proposed G1-006 on the
JSON-compatible product value domain, deterministic canonicalization/content identity, runtime
product-ID choice, bounded dynamic chain length, and early finalization. This closes the earlier
representation, predeclared-ID, and forced-full-chain portions of the control mismatch. However,
G1-006 still has an explicit `REJECTED -> discard_rejected -> READY -> propose` path driven by a
verifier FAIL, while G1-005 has no equivalent verifier-conditioned reject/discard/retry transition.
**EVIDENCE — external.** Leanabell-Prover-V2 explicitly uses multi-turn Lean verifier feedback to
correct errors and reports improved pass@128 after verifier-integrated training/search:
<https://arxiv.org/abs/2507.08649>.

**INFERENCE.** T22 is therefore narrower than in the previous audit, but it is not closed. The
remaining difference is scientifically central: verifier failure can decide whether a candidate is
discarded and additional compute is spent on a replacement. A win could reflect
verification-conditioned search/retry rather than the narrower claim that downstream consumers
benefit from a verified-product gate. If reject/retry is intentionally part of the treatment, the
claim should say so.

**Required falsifier/mitigation.** Freeze the estimand. For a *pure downstream gating* claim, add a
control with matched verifier queries/feedback and matched attempt/retry budget whose downstream
consumption policy does not depend on PASS/FAIL. For a broader *verified-gated search and
consumption* claim, predeclare that bundle and stop describing retry as a matched nuisance variable.
In either case charge every rejected attempt and retry to complete cost.

### T23 — Problem-family clustering can invalidate ordinary McNemar significance (`CRITICAL` if confirmatory items are clustered)

**EVIDENCE — repository.** `goal1/GOAL1.json` requires Holm-corrected exact paired tests. The current
`src/supernova_goal1/evaluate.py` and proposed G1-010 statistics helper reduce every candidate/control
comparison to two aggregate discordant counts (`candidate_only`, `control_only`) and apply ordinary
exact McNemar. Neither contract carries a benchmark-family/cluster identifier or a cluster-aware
variance/randomization rule. **EVIDENCE — external.** Eliasziw and Donner state that ordinary
McNemar assumes responses from matched pair to matched pair are mutually independent and develop an
adjustment for non-independent pairs: <https://onlinelibrary.wiley.com/doi/10.1002/sim.4780101211>.
Gönen likewise notes that clustered paired-binary data require adjustment for McNemar inference:
<https://pubmed.ncbi.nlm.nih.gov/15236431/>.

**INFERENCE.** If the eventual confirmatory set contains multiple semantic variants, theorem-family
siblings, generated perturbations, or other items sharing a common latent source, treating each item
as an independent McNemar pair can overstate effective sample size and make p-values anti-conservative.
This is distinct from T20 contamination: even perfectly unseen items can be statistically dependent.
The risk is especially relevant if robustness variants from T17 are included as if they were new
independent benchmark problems.

**Required falsifier/mitigation.** Before locking the benchmark, define the independent sampling
unit and freeze a family/cluster ID when items share a source or generated template. Either sample
one confirmatory item per independent family, aggregate to a predeclared family-level endpoint, or
use a cluster-aware paired test/randomization/bootstrap whose unit is the family. Do not count
structure-preserving variants as independent n. If clustering is uncertain, report a prospectively
specified sensitivity analysis under plausible family groupings.

## Minimum design conditions before a scientific Goal 1 run

The confirmatory run should not start until all of the following are frozen and inspectable:

1. benchmark identity, immutable split/hashes, allowed proof library, contamination policy, and benchmark-fidelity audit;
2. exact model/provider/version, prompts, sampling/seed policy, context limits, and tool permissions;
3. executable contracts for all five arms showing which causal features differ;
4. search-verifier and final-verifier identities, query/diagnostic budgets, verification granularity, and clean replay rules;
5. cross-arm/cross-problem isolation and execution randomization;
6. complete-cost accounting boundary plus an equal-cost or preregistered cost-frontier comparison, including generator and verifier computation;
7. retry, timeout, early-stop, residual-budget, and failure semantics;
8. replication/aggregation rule and the frozen paired statistical decision rule;
9. development/confirmatory separation preventing adaptive benchmark reuse;
10. prospective robustness checks against memorized-instance success when controlled variants are used;
11. model-version attestation and paired time blocking for hosted services;
12. inference-runtime reproducibility controls or an explicit hosted-backend uncertainty plan;
13. semantic/family-level contamination analysis with residual risk stated explicitly;
14. auditable hidden/reasoning-cost telemetry or a frozen uncertainty/sensitivity analysis;
15. contract-equivalent intermediate representation, chain-length, stopping, parentage, and finalization semantics for the primary verified-chain/product-only causal pair;
16. an explicit estimand for verifier-conditioned reject/retry: either matched as a nuisance variable or declared part of the treatment;
17. an independent benchmark sampling unit plus cluster/family-aware inference when multiple items share a latent source/template.

Passing implementation tests is necessary but not sufficient for these scientific conditions. The
adversarial question for every future change is: **could this change improve the verified-chain arm
without changing the intended verified-product mechanism?** If yes, the change is a potential
confound and needs either a matched control or a narrower claim.
