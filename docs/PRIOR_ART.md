# Goal 1 prior art and novelty boundary

Ticket: `G1-012` (`EXT01`)

This is an independent literature and construct-validity audit. It separates **EVIDENCE** (what a
repository artifact or direct/primary source actually establishes) from **INFERENCE** (what that
means for Supernova V2). It is not a novelty certificate, and no cited system is assumed to be a
fair control merely because it is related.

## Repository claim and the estimand that is currently defensible

**EVIDENCE — repository.** `goal1/GOAL1.json` asks whether a chain of independently verified
intermediate products solves more problems than ordinary, portfolio, product-only, and
multi-fidelity controls under one frozen cost budget. The active experiment remains `DRY_RUN` with
`cost_model_frozen=false`. `goal1/BENCHMARK.lock.json` now separately locks a 488-item miniF2F
composite by exact file hashes/root digest, but the active experiment contract does not yet bind that
content lock into its scientific records.

**EVIDENCE — repository.** The verified-chain contract can use verifier failure to reject/discard a
candidate and spend another attempt. The product-only control does not expose the same
verifier-conditioned reject/retry decision.

**INFERENCE.** The first confirmatory estimand should therefore be the **package effect of
verified-gated search and consumption** unless verifier-conditioned reject/retry is actually matched
across the relevant control. A positive result under the present causal distinction must not be
reported as a pure effect of intermediate-product gating. A later pure-gating estimand is possible
only with a control that matches verifier access/feedback, retry opportunity, attempt quota,
decomposition/state capacity and cost while changing only downstream consumption eligibility.

## Nearest mechanism prior art

### 1. Cobbe et al. (2021) — verifier-ranked candidate generation

Primary source: Karl Cobbe et al., *Training Verifiers to Solve Math Word Problems*.
<https://arxiv.org/abs/2110.14168>

**EVIDENCE.** Candidate solutions are generated and a learned verifier is used to rank/select them,
improving answer selection in the evaluated setting.

**INFERENCE for Goal 1.** Verification is already a search/selection capability. A portfolio denied
comparable verifier access is too weak to isolate verified-product consumption.

### 2. Lightman et al. (2023) — process feedback

Primary source: Hunter Lightman et al., *Let's Verify Step by Step*.
<https://arxiv.org/abs/2305.20050>

**EVIDENCE.** The paper studies supervision of intermediate reasoning steps and reports gains from
process supervision in its setting.

**INFERENCE.** Intermediate feedback is itself capability-relevant; “verifier present” is not a
matched control unless query frequency and feedback bandwidth are matched.

### 3. LeanDojo (2023) — reproducible Lean interaction and split discipline

Primary source: Kaiyu Yang et al., *LeanDojo: Theorem Proving with Retrieval-Augmented Language
Models*, NeurIPS 2023.
<https://proceedings.neurips.cc/paper_files/paper/2023/file/4441469427094f8873d0fecb0c4e1cee-Paper-Datasets_and_Benchmarks.pdf>

**EVIDENCE.** LeanDojo provides reproducible Lean interaction and a benchmark/split discipline meant
to probe generalization to novel premises.

**INFERENCE.** Formal-kernel interaction and retrieval-aware proving are prior art; proof-library and
retrieval visibility must be frozen before a generalization claim.

### 4. AlphaGeometry (2024) — neural proposals plus trusted symbolic reasoning

Primary source: Trieu H. Trinh et al., *Solving olympiad geometry without human demonstrations*.
<https://www.nature.com/articles/s41586-023-06747-5>

**EVIDENCE.** AlphaGeometry combines neural generation with symbolic deduction and reports
ablations/compute-aware comparisons.

**INFERENCE.** Alternating learned proposals and trusted symbolic operations is not novel by itself;
matched test-time resources and ablations are part of the scientific burden.

### 5. Snell et al. (2024) — test-time compute allocation changes performance

Primary source: Charlie Snell et al., *Scaling LLM Test-Time Compute Optimally can be More Effective
than Scaling Model Parameters*.
<https://arxiv.org/abs/2408.03314>

**EVIDENCE.** Search/revision policy and inference-compute allocation materially affect reasoning
performance and efficiency.

**INFERENCE.** Retry, early stopping, search depth and residual-budget reuse are treatment variables,
not incidental implementation details.

### 6. Seed-Prover, Prover Agent and Goedel-Architect — verified intermediate structure is prior art

Primary sources:
- Seed-Prover: <https://arxiv.org/abs/2507.23726>
- Prover Agent: <https://arxiv.org/abs/2506.19923>
- Goedel-Architect: <https://arxiv.org/abs/2606.06468>

**EVIDENCE.** These systems use formal-verifier feedback, proved auxiliary lemmas and/or formal
lemma dependency structures during proof search and downstream synthesis.

**INFERENCE.** Broad novelty claims such as “verified intermediate lemmas improve theorem proving”
or “generate, verify and reuse a lemma” are not defensible. Supernova's potential contribution is a
prospectively controlled causal comparison and/or a stricter evidence/transport contract.

### 7. DreamProver (2026) — cross-problem lemma-library evolution

Primary source: <https://arxiv.org/abs/2604.26311>

**EVIDENCE.** DreamProver builds and reuses transferable lemma libraries across theorem-solving
episodes.

**INFERENCE.** Cross-problem product reuse changes the estimand from a within-problem intervention to
transfer/library learning. Goal 1 should isolate those constructs.

### 8. Setlur et al.; Singhi et al.; Chen et al. — verification is a compute/information axis

Primary sources:
- Setlur et al., ICML 2025: <https://proceedings.mlr.press/v267/setlur25a.html>
- Singhi et al.: <https://arxiv.org/abs/2504.01005>
- Chen et al.: <https://arxiv.org/abs/2505.11730>

**EVIDENCE.** These works treat verifier access, solve-versus-verify allocation and verification
frequency as capability/efficiency variables.

**INFERENCE.** Equal generator calls do not establish a matched experiment if one arm receives more
verification computation or more frequent information.

### 9. Leanabell-Prover-V2 (2025) — verifier-conditioned correction is itself a capability

Primary source: <https://arxiv.org/abs/2507.08649>

**EVIDENCE.** Leanabell-Prover-V2 uses multi-turn Lean verifier feedback to correct proof
trajectories and reports improved proving performance in its evaluated setting.

**INFERENCE.** Supernova's `FAIL -> discard -> retry` path is scientifically substantive. Unless a
control receives the same verifier-conditioned retry opportunity, the first estimand is the broader
verified-gated search-and-consumption package.

## Benchmark leakage, fidelity and statistical prior art

### 10. LiveBench and MathArena — recent/fresh evaluation reduces contamination risk

Primary sources:
- LiveBench: <https://arxiv.org/abs/2406.19314>
- MathArena: <https://proceedings.neurips.cc/paper_files/paper/2025/file/1d27c01ebd3e3aebe226b44fc970d803-Paper-Datasets_and_Benchmarks_Track.pdf>

**EVIDENCE.** Both emphasize recent/frequently refreshed evaluation; MathArena reports contamination
signals on older public competition problems.

**INFERENCE.** Exact benchmark locking establishes what was tested, not whether a pretrained model
has seen equivalent material. Contamination risk remains a separate interpretive axis.

### 11. Spiesberger et al. (2026) — semantic duplicates can evade lexical decontamination

Primary source: <https://arxiv.org/abs/2602.12413>

**EVIDENCE.** Semantic duplicates can escape lexical/n-gram filtering and exposure to such material
can affect benchmark performance.

**INFERENCE.** Hash or n-gram cleanliness is not a certificate of structural generalization. This is
a robustness/sensitivity question unless clean out-of-distribution performance is itself part of
the primary claim.

### 12. Ammanamanchi, Bhat & Biderman (2026) — kernel-valid can be benchmark-invalid

Primary source: <https://arxiv.org/abs/2606.29493>

**EVIDENCE.** The paper reports mechanically certified defects in formal benchmarks, including
vacuity/counterexample/unsafe-axiom findings.

**INFERENCE.** Kernel proof replay is necessary but does not establish that the formalized task
faithfully measures the intended problem. Benchmark-fidelity checks remain distinct from verifier
correctness.

### 13. Eliasziw & Donner; Gönen — clustered paired data require adjusted inference

Primary sources:
- <https://onlinelibrary.wiley.com/doi/10.1002/sim.4780101211>
- <https://pubmed.ncbi.nlm.nih.gov/15236431/>

**EVIDENCE.** Ordinary McNemar inference assumes independence across matched pairs; clustered paired
binary outcomes require adjustment.

**INFERENCE.** The five arms per problem are **not** a sample size of five. The relevant concern is
whether benchmark problems themselves share latent families/templates. If they do, freeze the
independent sampling unit or use family/cluster-aware inference.

### 14. ASA p-value guidance and Lakens — significance is not effect magnitude

Primary sources:
- <https://doi.org/10.1080/00031305.2016.1154108>
- <https://doi.org/10.1525/collabra.33267>

**EVIDENCE.** Statistical significance does not itself measure scientific importance; sample-size
justification should follow the inferential target, precision or meaningful effect scale.

**INFERENCE.** Goal 1 should freeze a paired effect quantity and effect/precision interpretation in
addition to alpha and Holm correction.

### 15. Johari et al. and FDA group-sequential guidance — repeated looks need prospective error control

Primary sources:
- <https://pubsonline.informs.org/doi/10.1287/opre.2021.2135>
- <https://www.fda.gov/files/medical%20devices/published/Adaptive-Designs-for-Medical-Device-Clinical-Studies---Guidance-for-Industry-and-Food-and-Drug-Administration-Staff.pdf>

**EVIDENCE.** Fixed-horizon frequentist guarantees do not survive outcome-dependent repeated looks
without an appropriate sequential design/error-control rule.

**INFERENCE.** Holm correction across controls within one analysis does not license rerun-until-PASS.
Freeze one primary analysis or a valid sequential/alpha-spending procedure.

## Input, runtime and evidence-binding prior art

### 16. Sclar et al. (ICLR 2024) — prompt formatting can change measured performance

Primary source: <https://openreview.net/pdf?id=RIu5lyNXjT>

**EVIDENCE.** Meaning-preserving prompt-format changes cause substantial performance variation in
the evaluated models/tasks.

**INFERENCE.** A logical `problem_id` does not prove paired input equivalence. Common model-visible
input bytes/digests and explicit arm-specific prompt deltas should be frozen.

### 17. Chen/Zaharia/Zou; Yuan et al.; Ouyang et al. — hosted/runtime identity can drift or vary

Primary sources:
- hosted model change: <https://arxiv.org/abs/2307.09009>
- inference nondeterminism: <https://proceedings.neurips.cc/paper_files/paper/2025/hash/f80094a824ba5912d4a2de169c404a40-Abstract-Conference.html>
- run-to-run ChatGPT variation: <https://arxiv.org/abs/2308.02828>

**EVIDENCE.** Hosted behavior and backend numerical/execution conditions can change outputs even when
nominal settings appear stable.

**INFERENCE.** Paired arm order/time must be counterbalanced and runtime identity recorded; hosted
backend uncertainty cannot be erased by setting temperature to zero.

### 18. Python `subprocess` and Lean Elan — a command string is not a hermetic verifier identity

Direct sources:
- Python: <https://docs.python.org/3/library/subprocess.html>
- Lean Elan: <https://lean-lang.org/doc/reference/latest/Build-Tools-and-Distribution/Managing-Toolchains-with-Elan/>

**EVIDENCE.** `Popen(env=None)` inherits ambient environment, executable resolution depends on
context/platform, and Elan can select a Lean toolchain from project/directory context.

**INFERENCE.** Verifier command/timeout identity should be separate from, and joined to, a
content-addressed runtime/toolchain identity.

### 19. Lean proof validation and SLSA VSA — verification evidence should name its subject

Direct sources:
- Lean validation: <https://lean-lang.org/doc/reference/latest/ValidatingProofs/>
- SLSA VSA: <https://slsa.dev/spec/v1.2/verification_summary>

**EVIDENCE.** High-assurance proof validation checks the proof against the intended challenge; SLSA
VSA binds verification result, subject and verifier/policy information.

**INFERENCE.** Final-outcome evidence should bind challenge/problem digest, proof/artifact digest and
verifier receipt. That outcome/verifier binding is distinct from benchmark-content and runtime
bindings.

### 20. MLflow and Hugging Face dataset fingerprints — labels are not content identity

Direct sources:
- MLflow dataset tracking: <https://mlflow.org/docs/latest/dataset/>
- Hugging Face dataset fingerprints: <https://huggingface.co/docs/datasets/v1.16.0/about_cache.html>

**EVIDENCE.** Both systems use data digests/fingerprints in addition to human-readable names.

**INFERENCE.** `BENCHMARK.lock.json` should remain a distinct benchmark-content contract and be
joined to experiment/outcome records; it should not be folded into the proof receipt.

### 21. W3C Trace Context and OpenTelemetry — distributed evidence uses correlation identities

Direct sources:
- W3C Trace Context: <https://www.w3.org/TR/trace-context/>
- OpenTelemetry Trace API: <https://opentelemetry.io/docs/specs/otel/trace/api/>
- OpenTelemetry SDK: <https://opentelemetry.io/docs/specs/otel/trace/sdk/>

**EVIDENCE.** Distributed tracing uses stable trace/span identities to correlate causally related
work, and telemetry pipelines can drop records under bounded queues.

**INFERENCE.** Cost/dispatch evidence needs its own pre-dispatch execution identity and completeness
checks. A valid proof receipt does not prove its cost ledger, and a closed cost ledger does not prove
its benchmark subject unless the distinct contracts are explicitly joined.

## Cost construct and hosted/scheduled-chat measurement boundary

### 22. OpenAI API usage documentation — API usage can expose token accounting, but visible text is not the full meter

Direct sources:
- OpenAI, *How do I check my token usage?*: <https://help.openai.com/en/articles/6614209-how-do-i-check-my-token-usage>
- OpenAI, *Reviewing API usage and costs*: <https://help.openai.com/en/articles/10478918-reviewing-api-usage-and-costs>

**EVIDENCE.** OpenAI documents token-usage fields in API responses. Depending on endpoint/model,
usage details can include cached-input and reasoning-token counts; visible response length therefore
need not reveal full usage. For streamed Chat Completions, the final usage chunk can be absent if the
stream is interrupted, so missing usage is not evidence of zero usage.

**EVIDENCE — repository.** G1-107 explicitly targets scheduled-task execution where provider token
telemetry is unavailable.

**INFERENCE.** Supernova must distinguish an API/provider-metered cost surface from the scheduled
ChatGPT execution surface. Where raw provider usage is unavailable, exact canonical input/output byte
counts, dispatch counts, verifier/orchestration elapsed time, tool/retry events and optional
frozen-tokenizer estimates can form an **observable scheduled-chat proxy**, but they must not be
renamed provider tokens or physical compute.

### 23. Vaswani; Sarathi-Serve; DistServe — aggregate token totals do not identify physical compute

Primary sources:
- Transformer complexity: <https://papers.nips.cc/paper_files/paper/2017/hash/3f5ee243547dee91fbd053c1c4a845aa-Abstract.html>
- Sarathi-Serve: <https://www.usenix.org/conference/osdi24/presentation/agrawal>
- DistServe: <https://www.usenix.org/conference/osdi24/presentation/zhong-yinmin>

**EVIDENCE.** Sequence length, prefill/decode composition, batching and serving regime materially
change inference work/efficiency.

**INFERENCE.** Even exact provider token counts are a provider-metered proxy rather than an
architecture-independent physical-compute certificate. Request-shape uncertainty should be included
in sensitivity analysis when physical-compute fairness matters.

### 24. CoIn (2025) — hidden reasoning creates an auditability boundary

Primary source: <https://arxiv.org/abs/2505.13778>

**EVIDENCE.** The paper studies independent auditing of reasoning-token usage in opaque commercial
APIs.

**INFERENCE.** A hosted confirmatory claim needs a prospectively frozen hidden-cost sensitivity
analysis when hidden work is not independently observable.

### 25. Python time and Linux cgroup v2 — elapsed time is not resource-normalized compute

Direct sources:
- Python time: <https://docs.python.org/3/library/time.html>
- Linux cgroup v2: <https://kernel.org/doc/html/next/admin-guide/cgroup-v2.html>

**EVIDENCE.** Wall/performance time and CPU time are distinct; cgroup accounting separately reports
CPU use and throttling.

**INFERENCE.** Verifier/orchestration elapsed milliseconds are valid observables but not by
themselves physical-compute parity. Resource-normalized measures belong in robustness/sensitivity
unless the primary claim explicitly requires equal physical compute.

### 26. Provider rate-limit documentation — hosted serving capacity can couple arms

Direct sources:
- OpenAI rate limits: <https://platform.openai.com/docs/guides/rate-limits>
- Anthropic rate limits: <https://docs.anthropic.com/en/api/rate-limits>

**EVIDENCE.** Hosted APIs enforce shared request/token capacity and retry/throttling behavior at
account/project/model scopes.

**INFERENCE.** Concurrent paired cells may interfere through shared serving state. Isolate pools or
freeze a serialized/counterbalanced execution and infrastructure-failure policy.

## Phase separation: feasibility evidence is not confirmatory evidence

### 27. CONSORT pilot/feasibility extension — feasibility and definitive-effect questions are different

Direct source: Eldridge et al., *CONSORT 2010 statement: extension to randomised pilot and feasibility
trials*, BMJ 2016. <https://www.bmj.com/content/355/bmj.i5239>

**EVIDENCE.** The guidance distinguishes pilot/feasibility objectives from a later definitive test of
efficacy/effectiveness and states that formal effectiveness hypothesis testing is generally not the
purpose of an underpowered pilot.

**INFERENCE for Goal 1.** This is a methodological analogy, not a software-experiment authority. It
supports the phase discipline adopted in `THREAT_MODEL.md`: a dry/pilot cohort may validate
plumbing, observability, failure semantics and feasibility without earning scientific credit. A
CRITICAL threat can remain open during a non-credit pilot if the pilot does not claim to answer the
threatened scientific question.

`PASS`, `FAIL`, and `INCOMPLETE` remain empirical outcomes of an evaluator, not project phases.

## Benchmark-correction scope: content locking and semantic fidelity are different claims

### 28. AI-MO miniF2F corrections and miniF2F-Lean Revisited — “corrected” is not an exhaustive-fidelity certificate

Direct sources:
- AI-MO `minif2f_test` dataset card: <https://huggingface.co/datasets/AI-MO/minif2f_test>
- Ospanov, Farnia & Yousefzadeh, *miniF2F-Lean Revisited: Reviewing Limitations and Charting a Path Forward*: <https://arxiv.org/abs/2511.03108>

**EVIDENCE.** The AI-MO dataset card says it corrected **several erroneous formalizations** in the
DeepSeek-Prover-derived test set and lists concrete changed theorems. It does not state that every
test item received an exhaustive semantic-fidelity audit. Ospanov et al. independently report
formal/informal discrepancies for more than half of the original miniF2F problems and release a
separately corrected corpus after a full-benchmark analysis.

**EVIDENCE — repository.** Supernova's current locked test bytes come from the AI-MO corrected test
source; `BENCHMARK.lock.json` content-addresses those bytes but does not contain an item-level
semantic-fidelity attestation against the intended informal/source problems.

**INFERENCE.** Ospanov et al.'s defect rate must **not** be transferred numerically to the current
Kimina-corrected bytes; the sources are different revisions and that would exceed the evidence.
What the evidence does establish is a narrower assurance boundary: the word “corrected” plus a
content hash proves neither exhaustive correction nor source-problem fidelity. If Goal 1's primary
construct is exact Lean theorem-solving, Supernova can state that target narrowly and treat broader
Olympiad fidelity as robustness/interpretation. If the primary claim is about solving the intended
source mathematics, item-level semantic auditing or an independently audited corrected corpus must
be frozen before confirmation.

## Composable evidence boundary

The prior art supports **separation with explicit joins**, not one mega-certificate:

- benchmark/input provenance is a data-lineage contract (MLflow/Hugging Face analogy);
- runtime/toolchain identity is an execution-reproducibility contract (Python/Elan);
- final proof/outcome evidence is a subject-verification contract (Lean/SLSA);
- dispatch/cost completeness is an execution-correlation/accounting contract (W3C/OpenTelemetry).

**INFERENCE.** A confirmatory result should carry immutable subject identities that allow these
contracts to be joined and replayed while preserving their independent failure modes. No one binding
should silently attest the others.

## What the cited sources collectively establish

These are **EVIDENCE-backed observations**, not Supernova results:

- verifier selection/feedback and verified lemmas are established reasoning/search mechanisms;
- adaptive search, reject/retry and solve-versus-verify allocation can alter performance;
- public benchmark freshness, semantic overlap and formalization fidelity are distinct concerns;
- paired statistical validity depends on sampling structure and analysis/stopping rules;
- model-visible prompt representation and backend/runtime identity can affect outcomes;
- API token telemetry, wall time and physical compute are different measurement constructs;
- strong assurance systems bind evidence to explicit subjects and preserve provenance identities;
- pilot/feasibility evidence answers a different question from a definitive treatment-effect test.

## What these sources do **not** establish for Supernova

These are **INFERENCES/limits**:

1. None proves that Supernova's exact verified-gated package improves solve probability.
2. None proves a pure verify-before-consume effect under the current reject/retry mismatch.
3. A benchmark lock does not certify absence of training contamination or correct formalization.
4. A verifier PASS does not establish fair controls, cost parity or benchmark provenance.
5. A shared budget ceiling does not establish equal realized resources.
6. Hosted observable/provider-metered proxies do not establish equal physical compute.
7. A dry/pilot success is not a scientific PASS.

## Recommended first confirmatory statement to test, not assume

**INFERENCE / proposed framing.** Until verifier-conditioned reject/retry is matched, the defensible
question is:

> Under one prospectively frozen model/tool/runtime environment, benchmark/input contract,
> final-verifier evidence contract, dispatch/accounting contract, and common observable or
> provider-metered cost proxy, does the **verified-gated search-and-consumption package** increase
> paired final independently verified solve probability relative to each frozen control?

The result must report the cost proxy by its actual name and the prospective hidden/physical-compute
sensitivity analysis. It may be narrowed to a pure downstream verify-before-consume effect only after
the relevant verifier-conditioned search/retry opportunities are matched.

## Strong-control and sensitivity falsifiers

- **Verifier/search falsifier:** match verifier queries, feedback bandwidth, frequency and attempt/retry opportunity. (Cobbe; Lightman; Setlur; Leanabell.)
- **Decomposition/state falsifier:** match subgoal/product state capacity and search depth. (Seed-Prover; Prover Agent; Goedel-Architect.)
- **Cost falsifier:** compare matched realized proxy vectors, a frozen residual-budget policy, or a preregistered performance-versus-proxy-cost frontier. (Snell; Singhi.)
- **Hidden-compute sensitivity:** prospectively stress hidden reasoning, cache, request-shape, failed-call, verifier-resource and shared-serving asymmetries; report the smallest asymmetry that overturns the fairness interpretation. (OpenAI usage docs; CoIn; Sarathi-Serve; cgroup docs.)
- **Contamination/fidelity sensitivity:** preserve the primary locked analysis, then report fresh/semantic-overlap/formalization diagnostics without replacing an unfavorable primary result. If the primary construct itself includes source/Olympiad fidelity, upgrade semantic-fidelity validation from sensitivity to a pre-confirmatory condition. (LiveBench; MathArena; Spiesberger; Ammanamanchi; AI-MO miniF2F; Ospanov et al.)
- **Input/runtime falsifier:** bind one common problem/prompt payload and freeze/counterbalance model/runtime identity. (Sclar; Chen; Yuan; Python/Elan.)
- **Evidence-binding falsifier:** independently validate each benchmark, runtime, outcome/verifier and dispatch/cost binding, then join them by immutable subject IDs rather than trusting one aggregate certificate. (MLflow; Lean/SLSA; W3C/OpenTelemetry.)
- **Statistical falsifier:** freeze the independent sampling unit, effect interpretation, one-shot/sequential analysis rule and familywise correction before confirmatory outcomes. (Eliasziw & Donner; ASA/Lakens; Johari.)

If these primary blockers are not frozen, a positive result should be described as performance of an
incompletely controlled bundled system. If robustness-only tests fail after a valid primary run, the
primary estimate remains reportable but its interpretation must be narrowed accordingly.
