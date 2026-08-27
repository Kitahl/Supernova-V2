# Goal 1 prior art and novelty boundary

Ticket: `G1-012` (`EXT01`)

This is an independent literature audit for the Goal 1 claim. It distinguishes **EVIDENCE**
(what a cited source directly demonstrates or states) from **INFERENCE** (what that means for
Supernova V2). It is not a claim of novelty and it does not establish that any cited system is a
fair experimental control without reproducing its exact setup.

## Current Goal 1 claim

**EVIDENCE — repository.** `goal1/GOAL1.json` asks whether, within one problem, a chain of
independently verified intermediate products solves more problems than ordinary, portfolio,
product-only, and multi-fidelity controls under a frozen complete-cost budget.

**INFERENCE.** The scientifically defensible novelty target should be narrower than "verification
helps reasoning" or "verified intermediate lemmas help theorem proving." Both ideas already have
strong prior art. A potentially distinctive contribution would instead be a prospective causal
comparison that isolates *verify-before-consume chaining* from decomposition, verifier feedback,
search, and test-time compute while enforcing genuinely matched complete-cost conditions.

## Nearest direct prior art

### 1. Cobbe et al. (2021) — verifier-ranked candidate generation

Primary source: Karl Cobbe et al., *Training Verifiers to Solve Math Word Problems*.
<https://arxiv.org/abs/2110.14168>

**EVIDENCE.** The paper generates many candidate solutions at test time and trains a verifier to
rank/select among them. It reports that verification improves GSM8K performance and that verifier
scaling can be effective.

**INFERENCE for Goal 1.** A portfolio of independent attempts plus verifier-based selection is a
strong and directly relevant control. If the verified-chain treatment receives intermediate
verifier access while the portfolio is forced to select without an equivalent verifier budget, the
comparison does not isolate chaining. Supernova should therefore distinguish "verification as
selection" from "verification followed by downstream consumption."

### 2. Lightman et al. (2023) — process supervision

Primary source: Hunter Lightman et al., *Let's Verify Step by Step*.
<https://arxiv.org/abs/2305.20050>

**EVIDENCE.** The paper compares outcome supervision with process supervision that provides
feedback on intermediate reasoning steps, reporting stronger results from process supervision on a
representative subset of MATH and releasing PRM800K.

**INFERENCE for Goal 1.** Intermediate feedback is already known to be useful in at least some math
settings. This paper does not prove Supernova's proposed mechanism because the feedback is a
learned reward/process-supervision setup rather than a frozen formal kernel that mints consumable
verified products. It does, however, make a no-intermediate-feedback control too weak to support a
claim that *verification itself* caused any observed gain.

### 3. LeanDojo (2023) — reproducible Lean interaction and split discipline

Primary source: Kaiyu Yang et al., *LeanDojo: Theorem Proving with Retrieval-Augmented Language
Models*, NeurIPS 2023 Datasets and Benchmarks.
<https://proceedings.neurips.cc/paper_files/paper/2023/file/4441469427094f8873d0fecb0c4e1cee-Paper-Datasets_and_Benchmarks.pdf>

**EVIDENCE.** LeanDojo provides an open Lean environment for programmatic theorem proving,
retrieval-augmented proving, and a benchmark including a difficult split intended to test
generalization to theorems that rely on novel premises.

**INFERENCE for Goal 1.** Formal-kernel interaction and reproducible proof environments are mature
prior-art components, not unique Supernova mechanisms. LeanDojo's split design is also a warning
that random theorem splits can overstate generalization when premise/library overlap remains high.
Goal 1 should freeze its proof-library commit and contamination/generalization split explicitly.

### 4. AlphaGeometry (2024) — neuro-symbolic iterative reasoning with compute-matched baselines

Primary source: Trieu H. Trinh et al., *Solving olympiad geometry without human demonstrations*,
Nature 625 (2024).
<https://www.nature.com/articles/s41586-023-06747-5>

**EVIDENCE.** AlphaGeometry combines a neural language model that proposes auxiliary constructions
with a symbolic deduction engine. The paper reports ablations and explicitly describes matching a
strong baseline's test-time compute to AlphaGeometry for comparison.

**INFERENCE for Goal 1.** Alternating learned proposals with trusted symbolic reasoning is established
prior art. More importantly for Supernova, AlphaGeometry provides precedent for treating test-time
compute matching as part of a meaningful systems comparison. A common maximum ceiling alone is not
the same thing as a compute-matched control.

### 5. Snell et al. (2024) — test-time compute is itself a treatment variable

Primary source: Charlie Snell et al., *Scaling LLM Test-Time Compute Optimally can be More Effective
than Scaling Model Parameters*.
<https://arxiv.org/abs/2408.03314>

**EVIDENCE.** The paper studies search against process-based verifiers and adaptive revision under
fixed inference-compute budgets. It finds that the effectiveness of test-time compute strategies
depends on problem difficulty and reports substantial efficiency gains from adaptive allocation.

**INFERENCE for Goal 1.** Search depth, adaptive retries, verifier queries, and residual-budget
allocation cannot be treated as incidental implementation details. If the verified-chain arm has a
stronger adaptive allocation policy than its controls, the resulting effect mixes mechanism with
inference-time optimization. Goal 1 needs actual cost matching or a preregistered cost-performance
frontier.

### 6. LiveBench (2024) — contamination-resistant evaluation design

Primary source: Colin White et al., *LiveBench: A Challenging, Contamination-Limited LLM Benchmark*.
<https://arxiv.org/abs/2406.19314>

**EVIDENCE.** LiveBench was designed around frequently updated questions from recent sources and
objective ground-truth scoring to reduce contamination and judge bias.

**INFERENCE for Goal 1.** A fixed public math benchmark can become weak evidence for modern models
when training-data provenance is unavailable. Supernova should not call a benchmark
"contamination-free" merely because its test files are absent from this repository. Fresh/hidden
problems, release-time separation, or an explicit residual-risk statement are stronger options.

### 7. Seed-Prover (2025) — lemma-style proving with Lean feedback and proved lemmas

Primary source: Luoxin Chen et al., *Seed-Prover: Deep and Broad Reasoning for Automated Theorem
Proving*.
<https://arxiv.org/abs/2507.23726>

**EVIDENCE.** Seed-Prover is a lemma-style whole-proof reasoning system. Its abstract explicitly
states that it iteratively refines proofs using Lean feedback, proved lemmas, and self-summarization,
with multiple test-time strategies for deep and broad reasoning.

**INFERENCE for Goal 1.** This is close prior art to "generate an intermediate lemma, formally check
it, then use the proved result downstream." Supernova cannot defensibly claim that this broad
mechanism is new. Its differentiator, if experimentally supported, must lie in the rigor of the
controlled causal comparison, the exact verify-before-consume contract, or another narrower
mechanistic distinction.

### 8. Prover Agent (2025; later revisions through 2026) — formally proved auxiliary lemmas used in final synthesis

Primary source: Kaito Baba et al., *Prover Agent: An Agent-Based Framework for Formal Mathematical
Proofs*.
<https://arxiv.org/abs/2506.19923>

**EVIDENCE.** Prover Agent coordinates informal reasoning, a formal prover, and Lean feedback. It
generates auxiliary lemmas, proves them formally, and uses successfully proved lemmas to synthesize
the final proof.

**INFERENCE for Goal 1.** This is one of the closest published/preprint neighbors to the proposed
verified-chain concept. A Goal 1 result cannot establish novelty merely by showing that formally
verified intermediate lemmas can improve theorem proving. A stronger contribution would be to show
an incremental effect under matched models, verifier access, decomposition/search capability, and
complete cost, with ablations that distinguish chaining from ordinary verifier-guided refinement.

### 9. DreamProver (2026) — reusable lemma discovery and transfer

Primary source: Youyuan Zhang et al., *DreamProver: Evolving Transferable Lemma Libraries via a
Wake-Sleep Theorem-Proving Agent*.
<https://arxiv.org/abs/2604.26311>

**EVIDENCE.** DreamProver alternates proof search with abstraction/refinement of candidate lemmas to
build a transferable lemma library used on unseen theorems.

**INFERENCE for Goal 1.** DreamProver is more about cross-problem learning and reusable libraries
than a within-problem chain, but it matters for leakage boundaries. If Supernova lets verified
products persist across benchmark problems, it moves toward a transfer/library-learning treatment
and away from the current "within one problem" hypothesis. Goal 1 should therefore reset learned
product state between problems.

### 10. Goedel-Architect (2026) — verified lemma dependency graphs and refinement

Primary source: Jui-Hui Chung et al., *Goedel-Architect: Streamlining Formal Theorem Proving with
Blueprint Generation and Refinement*.
<https://arxiv.org/abs/2606.06468>

**EVIDENCE.** Goedel-Architect generates a dependency graph of formal definitions and lemmas leading
to the target theorem, dispatches Lean provers to close lemma nodes, and uses failures to refine the
global blueprint.

**INFERENCE for Goal 1.** By 2026, formal theorem-proving prior art includes not only serial
lemma-style reasoning but explicit verified dependency graphs with adaptive refinement. That makes
"verified intermediate structure" a crowded mechanism class. Supernova's serial chain may still be
scientifically useful, but its value must be demonstrated relative to strong graph/decomposition
and verifier-guided controls rather than framed as an unprecedented architecture.

### 11. Setlur et al. (ICML 2025) — verifier-based scaling is mechanistically different

Primary source: Amrith Setlur et al., *Scaling Test-Time Compute Without Verification or RL is
Suboptimal*, ICML 2025.
<https://proceedings.mlr.press/v267/setlur25a.html>

**EVIDENCE.** The paper gives theoretical and empirical arguments that verifier-based search/RL and
verifier-free approaches can scale differently even under fixed compute/data budgets, with the gap
depending on the underlying distribution of solution traces.

**INFERENCE for Goal 1.** Verifier access is not a cosmetic implementation detail. If the treatment
gets a different verification channel, frequency, or feedback semantics from a control, a measured
gain can be attributable to verifier-guided search rather than to downstream consumption of a
verified product. This strengthens the requirement for a verifier-matched non-chaining control.

### 12. Singhi et al. (2025) — solver-versus-verifier compute tradeoff

Primary source: Nishad Singhi et al., *When To Solve, When To Verify: Compute-Optimal Problem Solving
and Generative Verification for LLM Reasoning*.
<https://arxiv.org/abs/2504.01005>

**EVIDENCE.** The paper compares spending a fixed inference budget on additional solution generation
versus generative verification and reports that generative verification can require substantially
more compute to match self-consistency at practical budgets.

**INFERENCE for Goal 1.** "Verifier milliseconds" cannot be treated as a secondary resource while
model-generation tokens carry the scientific budget. Verification can itself dominate inference
cost. A fair portfolio control should be allowed to spend a matched total inference budget on more
solutions if the chain spends that budget on repeated verification.

### 13. MathArena (NeurIPS 2025) — real-time math evaluation and contamination evidence

Primary source: Mislav Balunović et al., *MathArena: Evaluating LLMs on Uncontaminated Math
Competitions*, NeurIPS 2025 Datasets and Benchmarks.
<https://proceedings.neurips.cc/paper_files/paper/2025/file/1d27c01ebd3e3aebe226b44fc970d803-Paper-Datasets_and_Benchmarks_Track.pdf>

**EVIDENCE.** MathArena evaluates models on newly released competition problems and reports strong
signs of contamination in AIME 2024 while using post-release timing to reduce memorization risk on
new competitions.

**INFERENCE for Goal 1.** For current frontier models, a familiar public math benchmark is weak
confirmatory evidence even if Supernova itself never stored the answers. Release-time separation
between the model and benchmark is a stronger leakage control. This also suggests that any
benchmark selected for Goal 1 should record problem publication dates and frozen model dates.

### 14. Ammanamanchi, Bhat & Biderman (2026) — formal benchmark defects survive kernel checking

Primary source: Pawan Sasanka Ammanamanchi, Siddharth Bhat, and Stella Biderman, *Faults in Our
Formal Benchmarking: Dataset Defects and Evaluation Failures in Lean Theorem Proving*.
<https://arxiv.org/abs/2606.29493>

**EVIDENCE.** The authors audit five Lean theorem-proving benchmarks and report thousands of findings,
including hundreds of mechanically certified defects such as vacuous theorems, counterexamples,
and unsound axioms. Their central point is that kernel checking proves the formal statement, not
that the statement faithfully captures the intended informal problem.

**INFERENCE for Goal 1.** Formal proof replay is necessary but not sufficient for construct validity.
A benchmark-quality audit must be a separate gate. Otherwise an apparently superior verified-chain
arm could exploit weakened, malformed, or degenerate formalizations and still obtain valid kernel
proofs.

### 15. Kwok et al. (2026) — verification as a distinct scaling axis

Primary source: Jacky Kwok et al., *LLM-as-a-Verifier: A General-Purpose Verification Framework*.
<https://arxiv.org/abs/2607.05391>

**EVIDENCE.** The paper explicitly frames verification as a scaling axis and uses fine-grained
feedback for agentic tasks.

**INFERENCE for Goal 1.** The information content of verifier responses must be part of the control
contract. A treatment receiving fine-grained verifier feedback is not fairly compared with an arm
receiving only a binary terminal score, even if both invoke a component called a "verifier."

### 16. Chen, Zaharia & Zou (2023) plus provider versioning docs — hosted-model identity can move over time

Primary sources: Lingjiao Chen, Matei Zaharia, and James Zou, *How is ChatGPT's behavior changing
over time?* <https://arxiv.org/abs/2307.09009>; Google, *Gemini API — Models*.
<https://ai.google.dev/gemini-api/docs/models>

**EVIDENCE.** Chen et al. compare March and June 2023 versions of GPT-3.5/GPT-4 and report substantial,
task-dependent behavior changes over a short interval. Google's current Gemini documentation makes
the operational versioning hazard explicit: specific stable model names are intended to be stable,
whereas `latest` aliases are hot-swapped to newer releases.

**INFERENCE for Goal 1.** Model family/name equality is not enough to establish treatment equality
across time. If arms are executed in separate temporal blocks against a moving hosted endpoint, a
provider update can be confounded with arm identity. Confirmatory execution should therefore use a
specific immutable/stable model version where available, record returned model identity and trusted
call time, and interleave/counterbalance paired arms so service drift cannot systematically favor one
mechanism.

### 17. Yuan et al. (NeurIPS 2025) and Ouyang et al. — deterministic settings do not guarantee reproducible inference

Primary sources: Jiayi Yuan et al., *Understanding and Mitigating Numerical Sources of
Nondeterminism in LLM Inference*, NeurIPS 2025.
<https://proceedings.neurips.cc/paper_files/paper/2025/hash/f80094a824ba5912d4a2de169c404a40-Abstract-Conference.html>;
Shuyin Ouyang et al., *An Empirical Study of the Non-determinism of ChatGPT in Code Generation*.
<https://arxiv.org/abs/2308.02828>

**EVIDENCE.** Yuan et al. show that batch size, GPU count, GPU version, and numerical precision can
change generated outputs and benchmark accuracy even under greedy decoding, with particularly
large effects for reasoning models. Ouyang et al. report substantial run-to-run variation in
ChatGPT code generation and explicitly find that temperature zero does not guarantee deterministic
outputs.

**INFERENCE for Goal 1.** A frozen seed and nominally deterministic decoding are not sufficient
experimental controls. Self-hosted confirmatory runs should freeze the serving/hardware/precision
stack; hosted runs should treat backend execution as a residual uncertainty source, randomize paired
arm order, and use a prospectively frozen replication/sensitivity plan. Otherwise backend execution
noise can be misread as a mechanism effect even when the advertised model version never changes.

## What the prior art collectively establishes

The following are **EVIDENCE-backed observations**, not Supernova results:

- verifier-based candidate selection can improve mathematical answer selection (Cobbe et al.);
- intermediate/process feedback can improve mathematical reasoning in some settings (Lightman et al.);
- LLMs can interact productively with formal proof assistants and retrieval systems (LeanDojo);
- neural proposal plus symbolic/formal reasoning is established (AlphaGeometry);
- test-time compute allocation materially affects measured performance (Snell et al.);
- verifier-based and verifier-free inference strategies can have different scaling behavior (Setlur et al.);
- allocating inference compute to verification versus additional solving can materially change efficiency (Singhi et al.);
- contamination-resistant benchmark design requires more than hiding local test files (LiveBench; MathArena);
- modern theorem provers already generate/refine lemmas and use formally proved intermediate facts downstream (Seed-Prover, Prover Agent, Goedel-Architect);
- reusable lemma discovery can create cross-problem state and transfer effects (DreamProver);
- a kernel-accepted Lean proof does not by itself establish that the benchmark statement faithfully encodes the intended problem (Ammanamanchi et al.);
- fine-grained verification feedback can itself be an agentic capability channel (Kwok et al.);
- hosted model services can change over time, and moving model aliases can silently change the data-generating system (Chen et al.; Gemini model-version documentation);
- backend execution details can induce output and accuracy variation even under nominally deterministic decoding (Yuan et al.; Ouyang et al.).

## What these sources do **not** establish for Supernova

The following are **INFERENCES/limits**:

1. None of the cited results proves that Supernova's exact `VerifiedProduct` contract causes a gain.
2. A formal checker PASS establishes acceptance by the frozen formal system; it does not by itself establish that the experimental controls were fair, the benchmark was semantically valid, or the benchmark was uncontaminated.
3. A stronger final solve rate under a shared maximum budget does not prove equal-cost superiority unless actual generator-plus-verifier resource use or a justified cost frontier is matched.
4. Existing prior art makes a broad novelty claim around "verified intermediate lemmas" untenable.
5. Existing prior art also makes "verification improves search" too broad a novelty claim.
6. A positive Goal 1 experiment could still be scientifically valuable if it isolates a narrower causal mechanism under stronger controls than prior systems report.

## Recommended novelty statement to test, not assume

**INFERENCE / proposed framing.** A defensible confirmatory question is:

> Under one frozen model/tool environment and prospectively matched complete-cost conditions, does
> enforcing a verify-before-consume contract for within-problem intermediate products increase
> paired final solve probability relative to controls that separately match decomposition,
> verifier-query access and feedback bandwidth, adaptive search, verification frequency, and
> intermediate-product generation without that exact contract?

This wording intentionally makes the contribution an empirical causal isolation claim, not a claim
that formal verification, lemma generation, process feedback, verifier-guided search, or
neuro-symbolic theorem proving was invented here.

## Strong-control implications from prior art

Before a scientific pass can support the mechanism claim, the frozen control suite should be able
to answer these prior-art-driven falsifiers:

- **Verifier-reranking falsifier:** can independent best-of-N candidates with the same verifier-query
  and feedback budget match the chain? (Cobbe et al.; Setlur et al.; Kwok et al.)
- **Process-feedback falsifier:** can equivalent intermediate feedback without a consumable verified
  object match the chain? (Lightman et al.)
- **Decomposition falsifier:** can the same lemma/subgoal decomposition and retries, but without
  verify-before-consume gating, match the chain? (Seed-Prover / Prover Agent / Goedel-Architect.)
- **Compute falsifier:** does the effect persist when actual generator-plus-verifier test-time
  resource use is matched rather than merely capped? (Snell et al.; AlphaGeometry; Singhi et al.)
- **Contamination falsifier:** does the effect persist on a fresh/hidden or otherwise prospectively
  protected split with model/problem release dates recorded? (LiveBench; MathArena; LeanDojo.)
- **Benchmark-fidelity falsifier:** do conclusions survive removal or correction of malformed,
  vacuous, weakened, or unsafe formal items? (Ammanamanchi et al.)
- **State-transfer falsifier:** does the result persist when every problem starts without reusable
  products or learned lemma state from previous benchmark items? (DreamProver.)
- **Temporal-drift falsifier:** does the effect persist when paired arms are interleaved or
  counterbalanced in narrow time blocks on the exact same stable model version, with returned model
  identity and call time recorded? (Chen et al.; Gemini model-version documentation.)
- **Inference-reproducibility falsifier:** does the effect persist across the prospectively frozen
  replication policy when self-hosted hardware/precision/serving settings are held fixed, or when
  hosted-backend uncertainty is explicitly randomized across paired arms? (Yuan et al.; Ouyang et al.)

If those falsifiers are not implemented, any positive result should be described as performance of
the *bundled verified-chain system* rather than evidence that verified-product chaining itself is
the cause.