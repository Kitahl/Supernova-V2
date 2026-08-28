# Goal 1 prior art and novelty boundary

Ticket: `G1-012` (`EXT01`)

This is an independent literature audit for the Goal 1 claim. It separates **EVIDENCE** (what a
cited primary/direct source demonstrates or states) from **INFERENCE** (what that means for
Supernova V2). It is not a novelty certificate and it does not assume any cited system is a fair
control without reproducing the relevant setup.

## Current Goal 1 claim

**EVIDENCE — repository.** `goal1/GOAL1.json` asks whether, within one problem, a chain of
independently verified intermediate products solves more problems than ordinary, portfolio,
product-only, and multi-fidelity controls under a frozen complete-cost budget.

**INFERENCE.** The defensible novelty target is narrower than "verification helps reasoning" or
"verified intermediate lemmas help theorem proving." Those mechanism classes already have strong
prior art. A potentially distinctive contribution would be a prospective causal comparison that
isolates *verify-before-consume* from decomposition, verifier feedback, adaptive search, retry,
representation, and test-time compute under genuinely matched conditions.

## Nearest direct prior art

### 1. Cobbe et al. (2021) — verifier-ranked candidate generation

Primary source: Karl Cobbe et al., *Training Verifiers to Solve Math Word Problems*.
<https://arxiv.org/abs/2110.14168>

**EVIDENCE.** The paper generates many candidate solutions and uses a trained verifier to rank or
select them, improving GSM8K answer selection.

**INFERENCE for Goal 1.** Verification-as-selection is already a strong baseline. A portfolio denied
comparable verifier access is too weak to isolate downstream verified-product consumption.

### 2. Lightman et al. (2023) — process supervision

Primary source: Hunter Lightman et al., *Let's Verify Step by Step*.
<https://arxiv.org/abs/2305.20050>

**EVIDENCE.** The paper studies feedback on intermediate reasoning steps and reports stronger process
supervision than outcome supervision in its evaluated setting.

**INFERENCE for Goal 1.** Intermediate feedback itself can be capability-relevant. A no-feedback
control cannot establish that a gain was caused specifically by a consumable verified product.

### 3. LeanDojo (2023) — reproducible Lean interaction and split discipline

Primary source: Kaiyu Yang et al., *LeanDojo: Theorem Proving with Retrieval-Augmented Language
Models*, NeurIPS 2023.
<https://proceedings.neurips.cc/paper_files/paper/2023/file/4441469427094f8873d0fecb0c4e1cee-Paper-Datasets_and_Benchmarks.pdf>

**EVIDENCE.** LeanDojo provides a reproducible Lean environment and a benchmark split designed to
probe generalization to novel premises.

**INFERENCE for Goal 1.** Formal-kernel interaction is prior art, and random theorem splits can be
weak when premise/library overlap remains high. Goal 1 should freeze library and retrieval scope.

### 4. AlphaGeometry (2024) — neuro-symbolic iteration with compute-aware comparison

Primary source: Trieu H. Trinh et al., *Solving olympiad geometry without human demonstrations*,
Nature 625 (2024).
<https://www.nature.com/articles/s41586-023-06747-5>

**EVIDENCE.** AlphaGeometry combines neural proposal generation with symbolic deduction and reports
ablations/compute-aware comparisons.

**INFERENCE for Goal 1.** Alternating learned proposals with trusted symbolic reasoning is not new.
The stronger lesson is that test-time compute matching belongs in the scientific comparison.

### 5. Snell et al. (2024) — test-time compute is itself a treatment variable

Primary source: Charlie Snell et al., *Scaling LLM Test-Time Compute Optimally can be More Effective
than Scaling Model Parameters*.
<https://arxiv.org/abs/2408.03314>

**EVIDENCE.** The work studies search/revision under inference-compute budgets and finds that
allocation strategy materially affects performance and efficiency.

**INFERENCE for Goal 1.** Search depth, retries, and residual-budget policy cannot be treated as
incidental when comparing a verified chain with controls.

### 6. LiveBench (2024) — contamination-limited evaluation design

Primary source: Colin White et al., *LiveBench: A Challenging, Contamination-Limited LLM Benchmark*.
<https://arxiv.org/abs/2406.19314>

**EVIDENCE.** LiveBench uses frequently refreshed recent questions and objective scoring to reduce
contamination and judge-bias risks.

**INFERENCE for Goal 1.** Absence of benchmark files from this repository is not evidence of absence
from model training. Fresh/hidden or post-cutoff data are stronger confirmatory evidence.

### 7. Seed-Prover (2025) — Lean feedback and proved lemmas used iteratively

Primary source: Luoxin Chen et al., *Seed-Prover: Deep and Broad Reasoning for Automated Theorem
Proving*.
<https://arxiv.org/abs/2507.23726>

**EVIDENCE.** Seed-Prover iteratively refines proof search using Lean feedback, proved lemmas, and
multiple test-time strategies.

**INFERENCE for Goal 1.** "Generate a lemma, formally check it, use it downstream" is already close
prior art. Supernova's differentiator must be a narrower controlled causal result or another exact
contractual distinction.

### 8. Prover Agent (2025/2026) — formally proved auxiliary lemmas used in final synthesis

Primary source: Kaito Baba et al., *Prover Agent: An Agent-Based Framework for Formal Mathematical
Proofs*.
<https://arxiv.org/abs/2506.19923>

**EVIDENCE.** Prover Agent coordinates informal reasoning, a formal prover, and Lean feedback,
proving auxiliary lemmas and using the proved lemmas in final synthesis.

**INFERENCE for Goal 1.** Broad novelty around verified intermediate lemmas is untenable. A useful
result would have to isolate an incremental effect against strong verifier/decomposition controls.

### 9. DreamProver (2026) — transferable lemma-library evolution

Primary source: Youyuan Zhang et al., *DreamProver: Evolving Transferable Lemma Libraries via a
Wake-Sleep Theorem-Proving Agent*.
<https://arxiv.org/abs/2604.26311>

**EVIDENCE.** DreamProver builds and reuses a transferable lemma library across theorem-solving
episodes.

**INFERENCE for Goal 1.** Cross-problem product reuse changes the estimand from a within-problem
mechanism to transfer/library learning. Goal 1 should reset learned product state between problems.

### 10. Goedel-Architect (2026) — verified lemma dependency graphs and refinement

Primary source: Jui-Hui Chung et al., *Goedel-Architect: Streamlining Formal Theorem Proving with
Blueprint Generation and Refinement*.
<https://arxiv.org/abs/2606.06468>

**EVIDENCE.** Goedel-Architect generates/refines dependency graphs of formal definitions and lemmas
and dispatches provers to close graph nodes.

**INFERENCE for Goal 1.** By 2026, verified intermediate structure includes adaptive graph-based
systems, not only serial chains. A serial verified chain should be compared against strong
verifier-guided decomposition/search rather than described as unprecedented.

### 11. Setlur et al. (ICML 2025) — verifier-based scaling is mechanistically different

Primary source: Amrith Setlur et al., *Scaling Test-Time Compute Without Verification or RL is
Suboptimal*, ICML 2025.
<https://proceedings.mlr.press/v267/setlur25a.html>

**EVIDENCE.** The paper gives theoretical and empirical evidence that verifier-based and verifier-free
inference strategies can scale differently even under fixed compute/data budgets.

**INFERENCE for Goal 1.** Verifier access/frequency/feedback semantics are experimental variables and
need matched controls before attributing a gain to verified-product consumption.

### 12. Singhi et al. (2025) — solve-versus-verify compute tradeoff

Primary source: Nishad Singhi et al., *When To Solve, When To Verify: Compute-Optimal Problem Solving
and Generative Verification for LLM Reasoning*.
<https://arxiv.org/abs/2504.01005>

**EVIDENCE.** The paper compares spending inference budget on additional solutions versus generative
verification and finds materially different compute requirements.

**INFERENCE for Goal 1.** Generator and verifier compute belong in one complete-cost comparison; a
control should be allowed to spend matched total resources on its strongest legal strategy.

### 13. MathArena (2025) — real-time math evaluation and contamination evidence

Primary source: Mislav Balunović et al., *MathArena: Evaluating LLMs on Uncontaminated Math
Competitions*, NeurIPS 2025.
<https://proceedings.neurips.cc/paper_files/paper/2025/file/1d27c01ebd3e3aebe226b44fc970d803-Paper-Datasets_and_Benchmarks_Track.pdf>

**EVIDENCE.** MathArena evaluates newly released competition problems and reports contamination
signals on older public competitions.

**INFERENCE for Goal 1.** Record model/problem release dates and prefer post-model-release
confirmatory items when feasible.

### 14. Ammanamanchi, Bhat & Biderman (2026) — kernel-valid can still be benchmark-invalid

Primary source: Pawan Sasanka Ammanamanchi, Siddharth Bhat, and Stella Biderman, *Faults in Our
Formal Benchmarking: Dataset Defects and Evaluation Failures in Lean Theorem Proving*.
<https://arxiv.org/abs/2606.29493>

**EVIDENCE.** The authors report thousands of findings in Lean benchmarks, including mechanically
certified vacuity, counterexample, and unsafe-axiom defects.

**INFERENCE for Goal 1.** Formal proof replay is necessary but not sufficient for construct validity;
benchmark-fidelity auditing must be a separate gate.

### 15. Kwok et al. (2026) — verification as a distinct scaling axis

Primary source: Jacky Kwok et al., *LLM-as-a-Verifier: A General-Purpose Verification Framework*.
<https://arxiv.org/abs/2607.05391>

**EVIDENCE.** The paper treats verification and fine-grained feedback as an agentic/scaling axis.

**INFERENCE for Goal 1.** Feedback bandwidth is part of treatment capability. "Both arms have a
verifier" is not enough if one receives richer or more frequent information.

### 16. Chen, Zaharia & Zou plus provider versioning docs — hosted-model identity can move

Primary sources: Lingjiao Chen, Matei Zaharia, and James Zou, *How is ChatGPT's behavior changing
over time?* <https://arxiv.org/abs/2307.09009>; Google, *Gemini API — Models*.
<https://ai.google.dev/gemini-api/docs/models>

**EVIDENCE.** Chen et al. measured task-dependent behavior changes across hosted GPT versions.
Google's documentation distinguishes specific stable names from moving `latest` aliases.

**INFERENCE for Goal 1.** Arm execution time must not be confounded with provider/model drift. Use
stable versions where possible and interleave/counterbalance paired arms.

### 17. Yuan et al. (NeurIPS 2025) and Ouyang et al. — deterministic settings do not guarantee reproducibility

Primary sources: Jiayi Yuan et al., *Understanding and Mitigating Numerical Sources of
Nondeterminism in LLM Inference*.
<https://proceedings.neurips.cc/paper_files/paper/2025/hash/f80094a824ba5912d4a2de169c404a40-Abstract-Conference.html>;
Shuyin Ouyang et al., *An Empirical Study of the Non-determinism of ChatGPT in Code Generation*.
<https://arxiv.org/abs/2308.02828>

**EVIDENCE.** These works show that backend/system factors and run-to-run variation can change model
outputs even under nominally deterministic settings.

**INFERENCE for Goal 1.** Self-hosted runtime fingerprints should be frozen; hosted runs need
randomized paired order plus a prospective replication/sensitivity policy.

### 18. Spiesberger et al. (2026) — semantic duplicates defeat lexical decontamination

Primary source: Ari Spiesberger et al., *Soft Contamination Means Benchmarks Test Shallow
Generalization*.
<https://arxiv.org/abs/2602.12413>

**EVIDENCE.** The paper shows semantic duplicates can escape n-gram filtering and can measurably
improve benchmark performance.

**INFERENCE for Goal 1.** Exact-match or n-gram decontamination is not a clean certificate. Family-
level/semantic overlap or post-cutoff data should be considered.

### 19. Sun et al. (2025) — hidden reasoning tokens are an auditability problem

Primary source: Guoheng Sun et al., *CoIn: Counting the Invisible Reasoning Tokens in Commercial
Opaque LLM APIs*.
<https://arxiv.org/abs/2505.13778>

**EVIDENCE.** The paper identifies the difficulty of independently auditing hidden reasoning-token
usage in commercial APIs and proposes a third-party auditing mechanism.

**INFERENCE for Goal 1.** Provider-reported usage is not automatically independently verified
complete cost. Preserve raw telemetry and uncertainty bounds or prefer auditable inference.

### 20. TheoremBench (2026) — intermediate representation/premises affect capability

Primary source: QuocViet Pham, Elvir Karimov, Andrey Galichin, and Ivan Oseledets,
*TheoremBench: Evaluating LLMs on Theorem Proving in Formal Mathematics*.
<https://arxiv.org/abs/2606.09450>

**EVIDENCE.** TheoremBench reports materially different proving performance when aligned tasks expose
supporting premises explicitly.

**INFERENCE for Goal 1.** Intermediate representation is not neutral plumbing. This motivated the
earlier G1-012 T22 concern. **Repository update:** current merged G1-005 / PR #12 now aligns the
product-only control with proposed G1-006 on JSON-compatible product values, deterministic
canonicalization/content identity, dynamic product IDs, bounded chain length, and early
finalization. Those representation/forced-chain portions of T22 are therefore no longer open. The
remaining mismatch is verifier-conditioned reject/discard/retry behavior.

### 21. Leanabell-Prover-V2 (2025) — verifier-conditioned correction is itself a capability

Primary source: Xingguang Ji et al., *Leanabell-Prover-V2: Verifier-integrated Reasoning for Formal
Theorem Proving via Reinforcement Learning*.
<https://arxiv.org/abs/2507.08649>

**EVIDENCE.** Leanabell-Prover-V2 uses multi-turn Lean verifier feedback, including success/error
signals, to correct proof trajectories and reports pass@128 improvements over its starting provers.

**INFERENCE for Goal 1.** A `FAIL -> discard -> retry` path is not a harmless implementation detail.
Verifier-conditioned retry can change search capability. If Supernova wants a *pure downstream
verify-before-consume* claim, a control must match verifier feedback/attempt budget without making
its downstream consumption decision depend on PASS/FAIL. If retry is intentionally part of the
treatment, the scientific claim should explicitly include verified-gated search.

### 22. Eliasziw & Donner (1991) and Gönen (2004) — ordinary McNemar requires independent matched pairs

Primary sources: Michael Eliasziw and Allan Donner, *Application of the McNemar test to
non-independent matched pair data*.
<https://onlinelibrary.wiley.com/doi/10.1002/sim.4780101211>; Mithat Gönen, *Sample size and power for
McNemar's test with clustered data*.
<https://pubmed.ncbi.nlm.nih.gov/15236431/>.

**EVIDENCE.** Eliasziw and Donner explicitly identify mutual independence from matched pair to
matched pair as an assumption of ordinary McNemar and develop an adjustment for non-independent
pairs. Gönen likewise treats clustered paired-binary data as requiring adjusted McNemar inference.

**INFERENCE for Goal 1.** `GOAL1.json` requires Holm-corrected exact paired tests, while the current
evaluator and proposed G1-010 reduce all benchmark items to aggregate discordant counts with no
cluster/family ID. If the confirmatory benchmark contains theorem-family siblings, generated
variants, or other shared-source items, ordinary problem-level McNemar can overstate the effective
sample size. Robustness variants should not silently count as independent n. Freeze the independent
sampling unit and use family-level or cluster-aware inference when needed.

### 23. Vaswani et al.; Sarathi-Serve; DistServe — token totals are not a compute invariant

Primary sources: Ashish Vaswani et al., *Attention Is All You Need*.
<https://papers.nips.cc/paper_files/paper/2017/hash/3f5ee243547dee91fbd053c1c4a845aa-Abstract.html>;
Amey Agrawal et al., *Taming Throughput-Latency Tradeoff in LLM Inference with Sarathi-Serve*, OSDI
2024. <https://www.usenix.org/conference/osdi24/presentation/agrawal>; Yinmin Zhong et al.,
*DistServe: Disaggregating Prefill and Decoding for Goodput-optimized Large Language Model Serving*,
OSDI 2024. <https://www.usenix.org/conference/osdi24/presentation/zhong-yinmin>.

**EVIDENCE.** The Transformer paper gives full self-attention complexity as dependent on sequence
length. Sarathi-Serve distinguishes compute-intensive prompt prefill from one-token-at-a-time decode
and demonstrates large serving-efficiency changes from batching/scheduling. DistServe independently
shows that prefill and decode have different resource and parallelism characteristics.

**INFERENCE for Goal 1.** Aggregate model-call/input-token/output-token totals are not an
architecture-independent compute meter. Two arms can match those totals while using different
request-length distributions, prefill/decode mixtures, batching/cache conditions, and therefore
actual accelerator work. Because chaining itself changes request segmentation, request shape can be
correlated with treatment. The confirmatory protocol should either measure/match model compute with
request-shape-aware telemetry or narrow the cost claim to a provider-metered token/call proxy.

### 24. OpenTelemetry SDK/Collector — telemetry completeness requires drop/provenance evidence

Primary sources: OpenTelemetry, *Tracing SDK* specification.
<https://opentelemetry.io/docs/specs/otel/trace/sdk/>; OpenTelemetry, *Collector internal telemetry*.
<https://opentelemetry.io/docs/collector/internal-telemetry/>.

**EVIDENCE.** The OpenTelemetry tracing SDK specifies a bounded batch-processor queue and states
that spans are dropped once `maxQueueSize` is reached. The Collector documentation exposes enqueue
and send-failure counters and recommends using ingress/egress telemetry to detect records that may
not have reached the backend.

**INFERENCE for Goal 1.** Telemetry can be internally well-formed yet incomplete. Proposed G1-007's
exact equality between observed events and a caller-supplied expected-event manifest therefore does
not by itself prove that every cost-bearing operation was represented. A stronger scientific cost
boundary needs trusted pre-execution dispatch provenance plus reconciliation to provider/runtime
evidence and explicit telemetry-drop detection.

### 25. OpenAI and Anthropic rate-limit specifications — hosted capacity is shared state

Primary sources: OpenAI, *Rate limits*.
<https://platform.openai.com/docs/guides/rate-limits>; Anthropic, *Rate limits*.
<https://docs.anthropic.com/en/api/rate-limits>.

**EVIDENCE.** OpenAI documents rate limits at organization/project scope, shared limits for some
model families, RPM/TPM-style capacity, and that unsuccessful requests still consume per-minute
capacity. Anthropic documents organization-level limits, short-burst enforcement, model-class
request/input/output-token limits, and cache-aware token accounting.

**INFERENCE for Goal 1.** Hosted serving capacity is experimental shared state. If paired arms share
a provider quota, queue, cache, or retry/backoff environment, activity from one arm can alter the
latency, throttling, failure rate, or effective retry opportunity of another even when conversation
state is perfectly isolated. Execution isolation and provider-fault handling therefore belong in the
confirmatory causal contract, not merely in operational monitoring.

### 26. Lean proof validation and SLSA VSA — a PASS claim should be bound to its exact subject

Primary sources: Lean, *Validating a Lean Proof*.
<https://lean-lang.org/doc/reference/latest/ValidatingProofs/>; SLSA, *Verification Summary
Attestation (VSA) v1.2*.
<https://slsa.dev/spec/v1.2/verification_summary>.

**EVIDENCE.** Lean's official validation guidance distinguishes a proof claim from independently
checking the proof term, and its high-assurance path replays the proposed proof against a trusted
challenge while ensuring that the proved theorem statement matches that challenge. SLSA's approved
VSA format analogously carries the artifact `subject` digest together with verifier identity/version,
verification policy, and verification result.

**INFERENCE for Goal 1.** These are engineering/assurance precedents rather than evidence that
Supernova's mechanism works, but they expose a missing scientific edge in the current repository:
`OutcomeRecord.verifier_passed` is a bare boolean consumed directly by `evaluate_experiment`, while
proposed G1-003's richer verifier result is not yet bound to the exact problem/proof artifact or to
the final outcome record. A confirmatory scientific result should therefore count a solved cell only
when the final verifier evidence is cryptographically or deterministically bound to the exact
challenge and submitted artifact, with verifier/policy identity preserved and independently
replayable.

### 27. MLflow and Hugging Face dataset fingerprints — dataset names are not content identity

Primary sources: MLflow, *ML Dataset Tracking*.
<https://mlflow.org/docs/latest/dataset/>; Hugging Face Datasets, *The cache — Fingerprint*.
<https://huggingface.co/docs/datasets/v1.16.0/about_cache.html>.

**EVIDENCE.** MLflow's dataset tracking represents a dataset with both a human-readable name and a
computed digest/hash plus source lineage. Hugging Face Datasets describes a fingerprint as tracking
the current dataset state: the initial fingerprint derives from the data files/table and subsequent
fingerprints change with transforms.

**INFERENCE for Goal 1.** These are reproducibility/data-lineage precedents, not evidence that
Supernova's mechanism works. They expose a repository-level identity gap: current Goal 1 scientific
records are keyed by logical benchmark/problem labels, while the proposed benchmark importer keeps
the benchmark-tree `root_sha256` in a separate lock file and `ExperimentSpec`/`OutcomeRecord` do not
bind that digest. A confirmatory record should therefore carry the exact benchmark-lock and split-
contract identity, and any preprocessing/formalization transform identity, so an outcome cannot be
replayed after the benchmark bytes change under the same names.

### 28. ASA p-value guidance and Lakens (2022) — statistical detectability is not effect magnitude

Primary sources: Ronald L. Wasserstein and Nicole A. Lazar, *The ASA's Statement on p-Values:
Context, Process, and Purpose*.
<https://doi.org/10.1080/00031305.2016.1154108>; Daniël Lakens, *Sample Size Justification*.
<https://doi.org/10.1525/collabra.33267>.

**EVIDENCE.** The ASA statement says that statistical significance does not measure effect size or
importance and warns against scientific conclusions based only on crossing a p-value threshold.
Lakens surveys prospective sample-size justification around the inferential target, including a
smallest effect size of interest, desired precision, and power/sensitivity.

**INFERENCE for Goal 1.** Current Goal 1 freezes an alpha threshold and a null-rejection rule but no
minimum paired solve-rate gain, precision target, or sensitivity/power criterion. Even if exact
McNemar and Holm are implemented perfectly, a PASS can identify a statistically detectable positive
difference without establishing a scientifically material one; conversely, a FAIL can be
uninformative when the confirmatory set is too small for the effect sizes that matter. The effect
quantity, smallest meaningful margin or explicit no-margin interpretation, and sample-size/
uncertainty plan should therefore be frozen before confirmation.

### 29. Sclar et al. (ICLR 2024) — semantically equivalent prompt formats can materially change measured performance

Primary source: Melanie Sclar, Yejin Choi, Yulia Tsvetkov, and Alane Suhr,
*Quantifying Language Models' Sensitivity to Spurious Features in Prompt Design or: How I Learned to
Start Worrying about Prompt Formatting*, ICLR 2024.
<https://openreview.net/pdf?id=RIu5lyNXjT>

**EVIDENCE.** Sclar et al. systematically vary meaning-preserving prompt formatting and report very
large performance spreads in some settings, including up to 76 accuracy points for LLaMA-2-13B,
with substantial sensitivity across tasks and models.

**INFERENCE for Goal 1.** Logical `problem_id` and benchmark identity are not sufficient to establish
paired-input equivalence. If arms can receive independently rendered problem statements or wrappers,
a prompt-formatting difference can masquerade as a mechanism effect. The confirmatory protocol
should bind every causal pair to one canonical common model-visible problem/prompt payload digest,
with only a separately frozen, explicit treatment-specific prompt delta allowed to differ.

### 30. Johari et al. and FDA group-sequential guidance — fixed-horizon inference is not optional-stopping-valid

Primary sources: Ramesh Johari, Pete Koomen, Leonid Pekelis, and David Walsh, *Always Valid
Inference: Continuous Monitoring of A/B Tests*, Operations Research 70(3), 2022.
<https://pubsonline.informs.org/doi/10.1287/opre.2021.2135>; U.S. Food and Drug Administration,
*Adaptive Designs for Medical Device Clinical Studies*, July 2016.
<https://www.fda.gov/files/medical%20devices/published/Adaptive-Designs-for-Medical-Device-Clinical-Studies---Guidance-for-Industry-and-Food-and-Drug-Administration-Staff.pdf>.

**EVIDENCE.** Johari et al. show that ordinary frequentist p-values and confidence intervals become
unreliable when users choose stopping/sample size by continuously monitoring results, and develop
always-valid inference for sequential decisions. FDA's group-sequential guidance is a separate
design precedent: planned interim looks and possible early stopping require prospective planning
while controlling the overall Type I error rate.

**INFERENCE for Goal 1.** Current Goal 1 freezes exact McNemar plus Holm across four controls for one
analysis, but does not freeze how many complete confirmatory analyses or whole-run reruns may be
examined. Holm within a look is not sequential error control. If a failed stochastic/nondeterministic
confirmatory run can be discarded and the same full-alpha analysis rerun until PASS, the advertised
familywise alpha no longer describes the overall scientific decision. Goal 1 therefore needs either
one immutable primary analysis or a prospectively valid group-sequential/alpha-spending/anytime-valid
rule covering every outcome-bearing look and rerun.

## What the prior art collectively establishes

The following are **EVIDENCE-backed observations**, not Supernova results:

- verifier-based candidate selection and process feedback can improve reasoning/search;
- formal proof assistants can be used interactively and intermediate proved lemmas are established prior art;
- adaptive test-time compute and solve-versus-verify allocation materially affect performance and cost;
- fresh/post-release benchmarks reduce but do not eliminate contamination risk;
- formal benchmark statements can be defective even when kernel-checkable;
- verifier feedback richness/frequency is itself a capability channel;
- hosted model identity and backend execution can drift or vary;
- semantic duplicates can defeat lexical decontamination;
- opaque hidden-reasoning telemetry can limit independent cost auditability;
- intermediate representation/premise exposure can affect prover capability;
- verification-conditioned feedback/retry can improve proof search;
- ordinary McNemar inference assumes independent matched pairs and needs adjustment for clustered pairs;
- aggregate token/call totals do not uniquely identify LLM inference compute or serving work;
- telemetry pipelines can drop records, so observed-trace consistency is not by itself a completeness certificate;
- hosted API quota/cache/queue state can be shared across requests, so concurrent experimental arms can interfere operationally;
- high-assurance verification practice binds a PASS to an exact artifact/challenge subject and verifier/policy evidence rather than trusting an unbound boolean;
- reproducible dataset tracking binds evaluation data to a digest/fingerprint and source lineage rather than relying on a dataset name alone;
- p-value threshold crossing does not by itself quantify effect magnitude or scientific importance, and sample-size justification should match the inferential target;
- meaning-preserving prompt formatting can materially change benchmark performance, so exact model-visible input identity is a causal experimental variable;
- fixed-horizon p-values require prospective sequential-error control when outcome-bearing looks or stopping decisions are repeated.

## What these sources do **not** establish for Supernova

These are **INFERENCES/limits**:

1. None proves that Supernova's exact `VerifiedProduct` contract causes a gain.
2. Checker PASS does not establish fair controls, benchmark fidelity, contamination cleanliness, or equal cost.
3. A shared maximum budget does not establish equal-cost superiority.
4. Existing prior art makes broad novelty claims around "verified intermediate lemmas" and "verification improves search" untenable.
5. A positive Goal 1 result could still be scientifically valuable if it isolates a narrower causal mechanism under stronger controls than prior systems report.

## Recommended novelty statement to test, not assume

**INFERENCE / proposed framing.** A defensible confirmatory question is:

> Under one frozen model/tool environment and prospectively matched complete-cost conditions, does
> enforcing a verify-before-consume contract for within-problem intermediate products increase
> paired final solve probability relative to controls that separately match decomposition,
> verifier-query access and feedback bandwidth, adaptive search/retry, verification frequency, and
> intermediate-product generation without that exact contract?

If verifier-conditioned reject/retry is part of the intended treatment rather than a nuisance
variable, replace "verify-before-consume" with an explicitly broader "verified-gated search and
consumption" claim before running the confirmatory experiment.

## Strong-control implications from prior art

Before a scientific pass supports the mechanism claim, the frozen design should answer:

- **Verifier-reranking falsifier:** can best-of-N/portfolio candidates with matched verifier access match the chain? (Cobbe; Setlur; Kwok.)
- **Process-feedback falsifier:** can equivalent intermediate feedback without a consumable verified object match it? (Lightman.)
- **Decomposition falsifier:** can the same subgoal/lemma decomposition under matched search budgets match it without the exact gate? (Seed-Prover; Prover Agent; Goedel-Architect.)
- **Retry-semantics falsifier:** is verifier-conditioned reject/discard/retry matched, or explicitly declared part of the treatment? (Leanabell-Prover-V2; Snell.)
- **Contract-equivalence falsifier:** are payload representation, parentage, chain length, stopping, and finalization matched across the primary causal pair? (TheoremBench.)
- **Compute falsifier:** does the effect persist at matched realized generator+verifier resources or a preregistered cost frontier? (Snell; AlphaGeometry; Singhi.)
- **Request-shape compute falsifier:** do conclusions survive request-shape-aware compute measurement/matching, or are they explicitly limited to a token/call budget claim? (Vaswani; Sarathi-Serve; DistServe.)
- **Dispatch-provenance falsifier:** does every cost-bearing model/verifier/tool operation receive a trusted pre-execution event ID and reconcile to provider/runtime evidence with telemetry-drop counters clean? (OpenTelemetry SDK/Collector.)
- **Contamination falsifier:** does the effect persist on fresh/hidden or prospectively protected items? (LiveBench; MathArena; LeanDojo.)
- **Soft-contamination falsifier:** does it survive semantic/family-level overlap checks or post-model-cutoff data? (Spiesberger.)
- **Benchmark-fidelity falsifier:** does it survive removal/correction of malformed or unsafe formal items? (Ammanamanchi et al.)
- **State-transfer falsifier:** does it persist with no cross-problem learned-product state? (DreamProver.)
- **Temporal/runtime falsifier:** does it persist with stable model identity, counterbalanced timing, and frozen/self-audited runtime conditions? (Chen; Gemini docs; Yuan; Ouyang.)
- **Opaque-cost falsifier:** does it persist under auditable hidden-reasoning usage or a predeclared sensitivity bound? (Sun et al.)
- **Cluster-dependence falsifier:** are benchmark-family/variant clusters identified and either reduced to independent sampling units or analyzed with cluster-aware paired inference? (Eliasziw & Donner; Gönen.)
- **Shared-serving interference falsifier:** do conclusions persist when paired cells are isolated from shared provider quotas/caches/queues, or under a frozen counterbalanced execution/throttling policy with complete retry/rate-limit telemetry? (OpenAI/Anthropic rate-limit docs.)
- **Outcome-evidence binding falsifier:** can every counted solved cell be replayed from an evidence-bound final-verifier record tied to the exact challenge and proof/artifact digest, with verifier/version/policy identity preserved? (Lean proof validation; SLSA VSA.)
- **Benchmark-content binding falsifier:** can every scientific record be joined to the exact benchmark-lock root digest, split-contract identity, and preprocessing/formalization identity, with mismatches rejected rather than accepted under the same logical problem labels? (MLflow; Hugging Face Datasets.)
- **Problem/prompt-payload identity falsifier:** are all paired cells bound to the same canonical common model-visible problem/prompt digest, with arm-specific prompt differences limited to a frozen explicit causal delta? (Sclar et al.)
- **Effect-size/sensitivity falsifier:** is the paired solve-rate effect, a justified smallest meaningful margin or explicit no-margin interpretation, and the confirmatory sample-size/uncertainty target frozen before outcomes are observed? (ASA p-value statement; Lakens.)
- **Sequential-look falsifier:** is there exactly one outcome-bearing confirmatory analysis, or a prospectively frozen sequential/anytime-valid procedure that controls error across every look/rerun? (Johari; FDA group-sequential guidance.)

If those falsifiers are not implemented, a positive result should be described as performance of the
*bundled verified-chain system* rather than evidence that verified-product chaining itself caused
the improvement.

### 31. Python subprocess and Lean Elan — a command string is not a hermetic verifier identity

Primary sources: Python Software Foundation, `subprocess.Popen` documentation.
<https://docs.python.org/3/library/subprocess.html>; Lean, *Managing Toolchains with Elan*.
<https://lean-lang.org/doc/reference/latest/Build-Tools-and-Distribution/Managing-Toolchains-with-Elan/>.

**EVIDENCE.** Python documents that `Popen(env=None)` inherits the current process environment and
that executable-path resolution is platform dependent; it recommends a fully qualified executable
path for maximum reliability. Lean's Elan documentation states that executables on `PATH` can be
proxies that select the active Lean toolchain from the current context/project, and recommends a
specific version in a checked-in `lean-toolchain` file for reproducible project use.

**INFERENCE for Goal 1.** Current merged G1-006 hashes only command template plus timeout into
`verifier_id`, and invokes G1-003 without a frozen cwd/environment. Therefore one nominal verifier
identity can resolve to different executable bytes, Lean toolchains, library checkouts, or ambient
configuration. An evidence-bound PASS remains under-specified if the checker implementation and
runtime inputs are not themselves content/environment bound. This is an assurance and causal-control
issue, not evidence that Supernova's mechanism succeeds or fails.

**Strong-control addendum — verifier-hermeticity falsifier.** Does every search/final verifier receipt
bind the resolved executable/toolchain, project/library snapshot, cwd, allowlisted environment, and
runtime/container identity, and does independent replay fail closed when any of those differ? Python
subprocess and Lean Elan semantics make this a necessary reproducibility check rather than an
optional metadata field.

### 32. Python time clocks and Linux cgroup CPU accounting — elapsed time is not CPU resource use

Primary sources: Python Software Foundation, `time` documentation.
<https://docs.python.org/3/library/time.html>; Linux kernel, *Control Group v2* documentation.
<https://kernel.org/doc/html/next/admin-guide/cgroup-v2.html>.

**EVIDENCE.** Python's standard-library documentation distinguishes a performance/elapsed clock from
CPU time: `perf_counter()` includes time elapsed during sleep, whereas `process_time()` measures
system plus user CPU time and excludes sleep. Linux cgroup v2 independently exposes CPU usage
(`usage_usec`, `user_usec`, `system_usec`) and throttling (`nr_throttled`, `throttled_usec`) as
separate accounting signals.

**INFERENCE for Goal 1.** These are operating-system/runtime accounting precedents, not evidence
about Supernova's mechanism. They expose a narrower cost-construct gap: current
`verifier_milliseconds` and `orchestration_milliseconds` are elapsed-duration fields. Equal elapsed
milliseconds can correspond to unequal CPU/core or accelerator work, while scheduler delay or
throttling can increase elapsed time without equivalent executed compute. The present fields are
therefore legitimate latency/accounting proxies but not, by themselves, a certificate of
resource-normalized compute parity.

**Strong-control addendum — non-model resource falsifier.** Are verifier/orchestration resource
allocations prospectively fixed and recorded, and does the cost record include an auditable
resource-normalized quantity such as cgroup/process CPU time plus accelerator usage where material?
If not, is the scientific claim explicitly limited to an elapsed-time/token proxy rather than
"complete compute" parity?

### 33. W3C Trace Context and OpenTelemetry — distributed evidence needs a shared correlation identity

Primary sources: W3C, *Trace Context* Recommendation.
<https://www.w3.org/TR/trace-context/>; OpenTelemetry, *Tracing API*.
<https://opentelemetry.io/docs/specs/otel/trace/api/>.

**EVIDENCE.** W3C Trace Context standardizes propagation of a unique trace identifier so an
individual request/transaction remains identifiable across participating distributed components and
trace data from multiple providers can be correlated. OpenTelemetry's `SpanContext` likewise carries
immutable `TraceId`/`SpanId` identifiers, propagates the trace identity across process boundaries,
and supports links between causally related spans.

**INFERENCE for Goal 1.** These are observability/provenance precedents, not evidence that Supernova's
mechanism works. They expose a repository seam: current `OutcomeRecord.cost` is a standalone
aggregate consumed by `evaluate_experiment`, while G1-007's closed event telemetry is not joined to
the experiment/problem/arm/replicate subject being scored. A cost report can therefore be complete
yet scientifically misattributed, or a cheap-looking outcome aggregate can be accepted without being
derived from the closed ledger.

**Strong-control addendum — cost-to-outcome binding falsifier.** Does every scored cell carry one
immutable execution/trace identity created before dispatch, shared by its model/verifier/orchestration
events and closed cost report, and does the evaluator derive/reconcile the cell's cost from that
report while rejecting missing, mismatched, duplicate, or replayed subjects?
