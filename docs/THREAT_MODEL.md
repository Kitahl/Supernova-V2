# Goal 1 threat model — independent adversarial audit

Ticket: `G1-012` (`EXT01`)

This document is an adversarial design review, not an admission certificate. It separates
**EVIDENCE** (repository state or an external primary/direct source) from **INFERENCE**
(the audit conclusion drawn from that evidence). A threat remains open until the experiment
has a prospective, machine-checkable falsifier or mitigation.

## Target claim and causal estimand

**EVIDENCE — repository.** `goal1/GOAL1.json` states the Goal 1 hypothesis as:
within one problem, a chain of independently verified intermediate products solves more
problems than ordinary, portfolio, product-only, and multi-fidelity controls under the same
frozen complete-cost budget. The bootstrap is explicitly `DRY_RUN`; its benchmark is
`UNSELECTED`/`UNPINNED`/`UNLOCKED`, and `cost_model_frozen=false`.

**INFERENCE.** A positive result only supports a mechanism claim about *verified intermediate
products* if the treatment and controls differ by that mechanism rather than by decomposition,
state persistence, search depth, retry policy, verifier access, prompt/context size, model/tool
access, or actual compute consumed. Otherwise the estimand is the performance of a bundled
agent architecture, which may still be useful but is a different claim.

## Threat ledger

### T1 — Treatment-bundle confounding (`CRITICAL`)

**EVIDENCE — repository.** Proposed PR `G1-006` (`#5`) gives the verified-chain arm a stateful
`propose -> verify -> consume -> propose` loop, permits discard/retry after a rejected product,
and allows downstream steps to consume a previously verified product. Proposed PR `G1-004`
(`#1`) deliberately gives the ordinary arm no intermediate-product channel and constrains the
portfolio arm to independent attempts with no cross-attempt shared context or synthesized final
answer.

**INFERENCE.** If those are the final semantics, treatment versus ordinary/portfolio changes
several variables simultaneously: decomposition, adaptive continuation, persistent state,
verifier feedback, and composition. A win would not identify which variable caused the win.

**Required falsifier/mitigation.** Before scientific execution, define controls/ablations that
match the treatment's model, tools, prompt budget, retry policy, decomposition opportunity, and
state capacity while removing one mechanism at a time. At minimum test (a) chaining with the
same intermediate products but verifier results withheld until final scoring, (b) verifier-guided
candidate selection without chained consumption, and (c) chaining of products that are produced
and consumed under the same budgets but not independently verified. Existing `product_only` and
`multi_fidelity` arms may satisfy parts of this requirement, but that must be demonstrated from
their frozen executable contracts rather than inferred from their names.

### T2 — Budget ceiling is not cost parity (`CRITICAL`)

**EVIDENCE — repository.** The bootstrap spec applies the same five-dimensional ceiling to all
arms. Proposed PR `G1-007` (`#2`) correctly refuses arbitrary scalar weights and checks each
component against the common ceiling, but it does not require the compared arms to consume equal
actual resources. **EVIDENCE — external.** Snell et al. show that test-time computation and how it
is allocated can materially change reasoning performance, including large efficiency differences
between test-time strategies: <https://arxiv.org/abs/2408.03314>.

**INFERENCE.** "Under the same ceiling" is weaker than "at equal complete cost." A treatment can
consume much more of several resource dimensions than a control and still satisfy the same ceiling.
That can turn a compute advantage into an apparent mechanism advantage.

**Required falsifier/mitigation.** Freeze one of these prospective rules: (1) exact per-problem
resource quotas that each arm may use; (2) a control policy allowed to spend all residual budget on
its strongest legal search strategy; or (3) predeclared performance-versus-cost frontiers with the
scientific comparison made only at matched resource vectors or justified exchange rates. Report
actual cost vectors for every `(problem, arm)` regardless of which rule is chosen.

### T3 — Intermediate verifier access is a privileged search oracle (`CRITICAL`)

**EVIDENCE — external.** Cobbe et al. generate many candidate solutions and use a verifier to
select among them, materially improving GSM8K performance: <https://arxiv.org/abs/2110.14168>.
Seed-Prover explicitly iterates on Lean feedback and proved lemmas:
<https://arxiv.org/abs/2507.23726>. **EVIDENCE — repository.** Proposed `G1-006` exposes PASS/FAIL
verification transitions to the chain during search.

**INFERENCE.** A treatment that can query the verifier during search while a control can only see
final scoring has extra information, not merely extra assurance. A gain could therefore be caused
by verifier-query access or feedback bandwidth.

**Required falsifier/mitigation.** Freeze verifier-query count, timeout, response vocabulary, and
error/diagnostic visibility per arm. Include a verifier-reranked portfolio/best-of-N control when
scientifically relevant. Separate the *search verifier channel* from the *blind final outcome
checker* in the data model, even if both replay through the same proof kernel.

### T4 — Distinct identity is not independent verification (`HIGH`)

**EVIDENCE — repository.** Proposed `G1-006` enforces `verifier_id != producer_id`.

**INFERENCE.** Two different strings do not establish independence. The producer and verifier
could still be the same model process, share hidden state, use the same untrusted parser, or rely on
the same unsound executable.

**Required falsifier/mitigation.** Freeze a verifier implementation/version/hash, trusted runtime,
allowed axioms/imports, timeout, and output contract. For formal proof tasks, replay final proofs in
a clean verifier process with the candidate arm unable to modify the verifier or its environment.
If a model-based verifier is ever used, record it as a different assurance class rather than treating
ID inequality as proof of independence.

### T5 — Benchmark contamination and benchmark-selection leakage (`CRITICAL` until pinned)

**EVIDENCE — repository.** The live Goal 1 bootstrap has no selected or locked benchmark.
**EVIDENCE — external.** LiveBench identifies test-set contamination as a threat to fair LLM
evaluation and uses frequently refreshed questions plus objective scoring to reduce that threat:
<https://arxiv.org/abs/2406.19314>. Deng et al. document contamination signals in modern LLM
benchmarks: <https://aclanthology.org/2024.naacl-long.482/>.

**INFERENCE.** Selecting or modifying the benchmark after seeing pilot arm behavior, or using a
public benchmark that may have been in model training/tuning data, can inflate apparent capability
and invalidate confirmatory statistics.

**Required falsifier/mitigation.** Pin benchmark identity, version, problem IDs, split, hashes, and
model version *before* treatment tuning on the confirmatory set. Prefer a hidden/fresh holdout or a
release-time split that postdates the frozen model where feasible. Keep benchmark answers/proofs
out of model-visible repository paths. Record any contamination audit and label residual
contamination risk as unknown rather than "clean" when provider training data are unavailable.

### T6 — Formal-library answer leakage (`HIGH`)

**EVIDENCE — external.** LeanDojo explicitly includes a split designed around novel premises and
provides a reproducible Lean environment: <https://proceedings.neurips.cc/paper_files/paper/2023/file/4441469427094f8873d0fecb0c4e1cee-Paper-Datasets_and_Benchmarks.pdf>.

**INFERENCE.** A theorem can be nominally held out while its exact statement, proof, a near-duplicate,
or a decisive helper lemma remains searchable in an allowed library or retrieval corpus. This is a
particularly serious construct threat if one arm gets broader retrieval/tool access than another.

**Required falsifier/mitigation.** Freeze the proof-library commit and retrieval corpus; scan for
exact/near-duplicate statements and known proofs; define whether previously available helper lemmas
are legal; apply identical retrieval visibility to all arms that are intended to be comparable.
Where the scientific question is generalization to new mathematical structure, include a
novel-premise/time-based split rather than relying only on random theorem splits.

### T7 — Cross-arm and cross-problem state leakage (`CRITICAL`)

**EVIDENCE — repository.** Proposed `G1-009` (`#6`) blinds evaluator-visible arm ordering, but the
current bootstrap does not yet contain a frozen execution-isolation contract.

**INFERENCE.** Blind scoring does not prevent an executor from carrying answers, failed attempts,
retrieved lemmas, caches, conversation state, or verifier diagnostics from one arm into another.
Likewise, reusable products learned on earlier benchmark problems would violate the stated
"within one problem" treatment unless such transfer is explicitly part of the estimand.

**Required falsifier/mitigation.** Execute each `(problem, arm, replicate)` in a fresh isolated
context with a frozen base image, prompt, tool policy, and cache policy. Randomize or counterbalance
execution order separately from evaluation order. Prohibit cross-problem learned-product reuse in
Goal 1 unless a later experiment explicitly studies transfer.

### T8 — Unequal model/tool/prompt capability (`CRITICAL`)

**EVIDENCE — repository.** The current Goal 1 spec freezes a budget ID but does not yet freeze a
model/provider/version, sampling policy, reasoning-effort setting, prompt template, context-window
policy, or external tool set.

**INFERENCE.** Any arm-specific difference in those variables can dominate the treatment effect.
"Same number of calls" is not equal capability if calls use different models, hidden reasoning
budgets, context lengths, or tools.

**Required falsifier/mitigation.** Freeze model/provider/version, sampling parameters and seed
policy, reasoning-effort mode, prompt templates, context limits, allowed tools/network, and retry
semantics. If heterogeneous models are scientifically intentional, predeclare them as part of the
treatment and stop describing the result as a pure verified-chain effect.

### T9 — Cost-accounting gaps and free work (`HIGH`)

**EVIDENCE — repository.** Proposed `G1-007` already identifies exclusions including energy,
storage/network, provider currency price, model-call latency, and depreciation, and requires
unknown telemetry not be silently converted to zero. It also proposes summing overlapping verifier
and orchestration durations rather than giving parallel work a free makespan discount.

**INFERENCE.** Remaining loopholes include hidden/reasoning tokens, provider-side cached tokens,
unreported failed requests, external tool compute, preprocessing, retrieval indexing, shared setup,
and any human intervention. A treatment-specific service can become a hidden subsidy.

**Required falsifier/mitigation.** Predeclare the accounting boundary. Charge all treatment-specific
setup and tool calls; record cached and uncached token semantics; count issued retries/failures;
fail closed on missing telemetry. Report both cumulative resource use and wall-clock latency if a
claim about practical efficiency is made. Do not silently amortize setup across benchmark problems
unless every arm receives the same amortization rule.

### T10 — Early stopping and retry asymmetry (`HIGH`)

**EVIDENCE — external.** Test-time compute strategies can allocate effort adaptively by problem
difficulty, and this changes efficiency: <https://arxiv.org/abs/2408.03314>.

**INFERENCE.** Allowing the verified chain to retry after informative verifier failures while a
control has a fixed number of non-adaptive attempts can create an unfair advantage even under the
same maximum budget. Conversely, forcing all arms to spend the maximum after success would distort
practical efficiency.

**Required falsifier/mitigation.** Pre-register success stopping, timeout, retry, and residual-budget
reuse rules. Controls must receive the strongest analogous adaptive allocation that is legal under
their mechanism definition. Record unused budget rather than treating it as consumed.

### T11 — Verifier overfitting and diagnostic-channel exploitation (`HIGH`)

**EVIDENCE — external.** Modern formal-proving systems such as Prover Agent and Seed-Prover use Lean
feedback iteratively, demonstrating that verifier diagnostics can be an active reasoning signal:
<https://arxiv.org/abs/2506.19923> and <https://arxiv.org/abs/2507.23726>.

**INFERENCE.** If treatment search receives rich diagnostics from the same checker that determines
success, it can learn checker-specific workarounds or exploit an implementation bug. A final PASS
then proves acceptance by that checker, not necessarily the broader intended mathematical construct.

**Required falsifier/mitigation.** Use a narrow, frozen search-verifier response contract where
possible and independently replay final artifacts in a clean environment. Reject unsafe axioms,
`sorry`, oracle imports, environment mutation, and non-reproducible network dependencies. Preserve
raw verifier evidence for audit.

### T12 — Stochastic seed luck and undefined replication (`HIGH`)

**EVIDENCE — repository.** Goal 1 uses binary solved outcomes and paired McNemar comparisons by
problem, but the current bootstrap does not define how stochastic generation replicates are reduced
to one binary outcome per `(problem, arm)`.

**INFERENCE.** With stochastic arms, a single draw can make the problem-level binary outcome depend
on seed luck. Retrying only the treatment or choosing favorable seeds after observation creates a
researcher degree of freedom.

**Required falsifier/mitigation.** Freeze the seed/replication policy before confirmatory execution.
If multiple samples are allowed, define the per-problem aggregation rule and charge every sample.
Never rerun only losing cells unless the same deterministic retry rule applies to every arm.

### T13 — Confirmatory-set reuse and adaptive overfitting (`HIGH`)

**EVIDENCE — inference from standard experimental separation.** The repository is being actively
built while the final benchmark is not yet locked.

**INFERENCE.** Repeatedly inspecting confirmatory failures and modifying prompts, decomposition,
verifier interfaces, or budgets against those same problems turns the holdout into development data
even if the files are never used for training.

**Required falsifier/mitigation.** Maintain separate development and confirmatory sets. Permit
unlimited debugging only on development data; expose the confirmatory set once under the frozen
protocol, or use a predeclared limited retest rule with a new untouched holdout after substantive
changes.

### T14 — Dry-run evidence accidentally promoted to science (`BLOCKER if misreported`)

**EVIDENCE — repository.** The current experiment is `goal1-bootstrap-dry-run`, has only two required
problem IDs, an unselected benchmark, and an unfrozen cost model.

**INFERENCE.** The dry run can validate schemas, transport, failure handling, and evaluator behavior,
but it cannot support the scientific hypothesis.

**Required falsifier/mitigation.** Preserve an explicit gate that reports scientific evaluation as
`BLOCKED` until benchmark/split and complete-cost semantics are frozen. Never quote dry-run solve
counts as evidence for the mechanism.

### T15 — Kernel-valid can still be benchmark-invalid (`CRITICAL` for formal benchmarks)

**EVIDENCE — external.** Ammanamanchi, Bhat, and Biderman (2026) audited five Lean theorem-proving
benchmarks and reported 4,833 findings, including 398 mechanically certified issues such as
counterexamples, vacuous theorems, and unsound axioms. They emphasize that the Lean kernel proves a
formal statement, not that the formal statement faithfully represents the intended informal
problem: <https://arxiv.org/abs/2606.29493>.

**INFERENCE.** Goal 1 cannot use "kernel verified" as a synonym for "scientifically correct benchmark
solve." A malformed or weakened formalization can make an arm look successful while measuring the
wrong construct. This is distinct from model contamination: the benchmark itself may be defective.

**Required falsifier/mitigation.** Before freezing a formal benchmark, run mechanical dataset checks
for vacuity, unsafe axioms/imports, contradictory assumptions, degenerate arithmetic semantics, and
other executable loopholes; maintain a reviewed mapping from informal problem identity to formal
statement; freeze corrected snapshots/hashes; and report any excluded or repaired items
prospectively. Final proof replay must be paired with benchmark-fidelity validation, not substituted
for it.

### T16 — Verifier computation and verifier granularity are treatment variables (`CRITICAL`)

**EVIDENCE — external.** Setlur et al. (ICML 2025) show that verifier-based search/RL can scale
differently from verifier-free methods even under fixed compute/data budgets:
<https://proceedings.mlr.press/v267/setlur25a.html>. Singhi et al. (2025) explicitly account for the
solve-versus-verify tradeoff and report that generative verification can require substantially more
inference compute than self-consistency at practical budgets:
<https://arxiv.org/abs/2504.01005>. Chen et al. (2025) further show that verification frequency
changes both accuracy and FLOP efficiency: <https://arxiv.org/abs/2505.11730>.

**INFERENCE.** Counting only generator calls/tokens while putting verifier computation in a separate,
loose ceiling leaves a major cost loophole. Two arms can have the same nominal model budget while
one obtains more total inference FLOPs and more information by verifying more often. The frequency
and richness of verification are part of the treatment, not neutral bookkeeping.

**Required falsifier/mitigation.** Log and cap verifier invocations, verifier input/output tokens or
FLOPs where applicable, wall time, diagnostics returned, and verification granularity. Report a
joint generator+verifier cost vector for every cell. For any causal claim about chaining rather than
verification intensity, include a control with matched verifier frequency/information bandwidth but
without verified-product consumption.

### T17 — Public-benchmark success may be instance memorization rather than structural reasoning (`HIGH`)

**EVIDENCE — external.** MathArena evaluates models on newly released math competitions and reports
strong signs of contamination on AIME 2024, motivating real-time post-release evaluation:
<https://arxiv.org/abs/2505.23281>. VAR-MATH converts fixed public math questions into multiple
symbolic instantiations and reports large performance drops on variabilized versions of common math
benchmarks: <https://arxiv.org/abs/2507.12885>.

**INFERENCE.** Even if a public benchmark is frozen before Supernova tuning, a model may exploit
memorized instances or surface patterns. A verified final artifact does not distinguish memorized
retrieval from robust reasoning. This matters especially if Goal 1 is framed as a general reasoning
mechanism rather than a benchmark-engineering result.

**Required falsifier/mitigation.** Prefer post-model-release or hidden confirmatory items. Where
problem families permit it, add prospectively generated semantic/symbolic variants that preserve the
intended proof structure while changing incidental constants/names, and require consistency across
variants. Treat these as a robustness diagnostic with a predeclared aggregation rule rather than
silently replacing the primary endpoint after results are seen.

### T18 — Hosted-model time drift can masquerade as an arm effect (`CRITICAL` for API models)

**EVIDENCE — repository.** `goal1/GOAL1.json` currently freezes no model/provider/version and contains
no execution-time blocking rule. **EVIDENCE — external.** Chen, Zaharia, and Zou measured materially
different task behavior between March and June 2023 versions of GPT-3.5/GPT-4, showing that a hosted
LLM service can change over time: <https://arxiv.org/abs/2307.09009>. Current Gemini API documentation
states that `latest` model aliases are hot-swapped on new releases, while specific stable model names
are intended to be stable: <https://ai.google.dev/gemini-api/docs/models>.

**INFERENCE.** If one arm is run earlier and another later, calendar time can become correlated with
arm identity. A provider alias rollover or other backend/model change can then look like a verified-
chain treatment effect even though the repository, prompt, and nominal model string did not change.
This threat is distinct from ordinary sampling noise because the data-generating model itself can
move during the experiment.

**Required falsifier/mitigation.** Use an immutable/specific provider model version rather than a
moving `latest` alias wherever possible; record the requested and provider-returned model/version
identifier plus trusted call time; execute paired arms for each problem in randomized or
counterbalanced narrow time blocks; and fail closed or segment a confirmatory run if model identity
changes inside a block. If the provider cannot attest an immutable model identity, predeclare that
limitation and make time blocking part of the design rather than assuming a repeated alias denotes
the same treatment.

### T19 — "Deterministic" inference can still vary with backend execution (`HIGH`)

**EVIDENCE — repository.** `goal1/GOAL1.json` does not freeze an inference-runtime fingerprint such
as batch size, accelerator type/count, numerical precision, serving engine, or deterministic-kernel
policy. **EVIDENCE — external.** Yuan et al. show that changing batch size, GPU count, or GPU version
can change reasoning-model outputs and benchmark accuracy even under greedy decoding; their NeurIPS
2025 study reports up to 9% accuracy variation for one reasoning model under BF16 across system
configurations: <https://proceedings.neurips.cc/paper_files/paper/2025/hash/f80094a824ba5912d4a2de169c404a40-Abstract-Conference.html>.
Ouyang et al. independently report substantial run-to-run non-determinism for ChatGPT code
generation and find that temperature zero does not guarantee deterministic outputs:
<https://arxiv.org/abs/2308.02828>.

**INFERENCE.** Freezing a model name, prompt, temperature, and seed does not by itself make paired
arm outcomes reproducible. If backend batching, hardware, precision, or serving kernels differ
across arms or replicates, system-level numerical variation can be mistaken for treatment variation.
This is distinct from T18's model-version drift: the advertised model can stay fixed while its
execution path changes.

**Required falsifier/mitigation.** For self-hosted inference, freeze and record the model weights,
serving engine/version, accelerator type/count, batch size, numerical precision, parallelism, and
kernel/determinism settings. For hosted APIs where those details are unavailable, do not label a
seed or temperature-zero run "deterministic" unless the provider guarantees it; randomize paired
arm order, use a prospectively frozen replication policy, retain request/response provenance when
available, and run a sensitivity analysis showing that the scientific conclusion is not driven by
single-run backend noise.

## Minimum design conditions before a scientific Goal 1 run

The confirmatory run should not start until all of the following are frozen and inspectable:

1. benchmark identity, immutable split/hashes, allowed proof library, contamination policy, and a benchmark-fidelity audit;
2. exact model/provider/version, prompts, sampling/seed policy, context limits, and tool permissions;
3. executable contracts for all five arms showing which single causal features differ;
4. search-verifier and final-verifier identities, query/diagnostic budgets, verification granularity, and clean replay rules;
5. cross-arm/cross-problem isolation and execution randomization;
6. complete-cost accounting boundary plus an equal-cost or predeclared cost-frontier comparison rule, including generator and verifier computation;
7. retry, timeout, early-stop, and failure semantics;
8. replication/aggregation rule and the already-frozen paired statistical decision rule;
9. a development/confirmatory separation that prevents adaptive benchmark reuse;
10. a prospective robustness check against memorized-instance success when the benchmark design permits controlled variants;
11. model-version attestation plus paired time blocking that prevents moving provider aliases or backend drift from correlating with arm identity;
12. inference-runtime reproducibility controls: either a frozen self-hosted hardware/software/precision fingerprint or an explicit hosted-backend uncertainty plan with randomized paired execution and replication sensitivity.

Passing implementation tests is necessary but not sufficient for these scientific conditions. The
adversarial question for every future change is: **could this change improve the verified-chain arm
without changing the intended verified-product mechanism?** If yes, the change is a potential
confound and needs either a matched control or a narrower claim.