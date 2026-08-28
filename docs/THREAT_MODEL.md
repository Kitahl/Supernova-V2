# Goal 1 threat model — independent adversarial audit

Ticket: `G1-012` (`EXT01`)

This document is an adversarial design review, not an admission certificate. It separates
**EVIDENCE** (repository state or a primary/direct external source) from **INFERENCE** (the audit
conclusion drawn from that evidence). Threat severity is epistemic; it is **not** automatically a
workflow gate. The phase table below is the sequencing authority for this document.

## Current repository state

**EVIDENCE — repository.** `goal1/GOAL1.json` is still `DRY_RUN`, names an
`UNSELECTED`/`UNPINNED`/`UNLOCKED` active benchmark, and has `cost_model_frozen=false`. Separately,
`goal1/BENCHMARK.lock.json` now locks a 488-problem miniF2F composite by file hashes and
`root_sha256`, but the active `GOAL1.json` does not yet bind that lock into the scientific
experiment contract.

**INFERENCE.** The current repository can support plumbing and feasibility work, but the dry cohort
cannot earn scientific credit. The existence of a benchmark lock beside the dry experiment is
progress, not evidence that the current experiment is already content-bound or confirmatory-ready.

## First confirmatory estimand

**EVIDENCE — repository.** The verified-chain execution contract has a verifier-conditioned
`FAIL -> reject/discard -> retry` path. The product-only control does not expose an equivalent
verifier-conditioned retry decision. This mismatch is T22 below.

**INFERENCE — primary estimand.** Unless that reject/retry decision is actually matched across the
relevant control, the first confirmatory estimand is the **package effect of verified-gated search
and consumption**, not a pure intermediate-product or pure verify-before-consume effect. For each
control `c`, the paired effect of interest is the difference in final independently verified solve
probability between the frozen verified-gated package and `c`, under the frozen cost construct.
Verifier-conditioned rejection, retry allocation, and downstream consumption are part of that
package.

A narrower **pure gating** estimand is admissible only after a control matches verifier query count,
feedback bandwidth, reject/discard/retry opportunity, attempt quota, decomposition/state capacity,
and cost while differing on whether unverified intermediate products may be consumed downstream.
Until then, documentation must not convert a package win into a pure mechanism claim.

## Phase-ranked gate matrix — authoritative sequencing

This table **supersedes the former blanket “minimum design conditions before a scientific Goal 1
run” checklist as workflow sequencing**. The old threat numbers remain useful audit identifiers, but
not every mitigation is a prerequisite to run any informative experiment.

| Phase rank | What may run | Required before that phase | What the phase may establish |
| --- | --- | --- | --- |
| **DRY/PILOT** | Schema, transport, verifier, benchmark-import, dispatch, cost-observability and five-arm plumbing tests. A small non-credit execution may run with scientific threats still open if those threats are recorded rather than silently converted to PASS. | Exact run identity; no secret/private leakage; deterministic input/record serialization where claimed; explicit missing/unknown states; no reuse of dry results as confirmatory evidence. For a pilot that exercises real models, preserve raw requests/responses, verifier evidence and observable cost proxies so seam failures can be diagnosed. | Feasibility, failure modes, missing telemetry, runtime incompatibilities, control-contract bugs, and power/discordance planning inputs. **No scientific credit.** |
| **CONFIRMATORY-BLOCKING** | One prospectively frozen primary confirmatory experiment. | The causal estimand and arm contracts; benchmark/split/content binding; development/confirmatory separation; exact common problem/prompt identity; model/tool/runtime identity; verifier access/feedback and retry semantics; cross-arm isolation; final-verifier evidence binding; trusted dispatch/cost-to-outcome join; a named cost construct with symmetric ceilings/allocation; sampling unit and statistical plan; effect/precision target; one-shot or valid sequential-look rule. Conditional blockers apply when the design uses clustered families, hosted shared quotas, or another identified dependency. | Only the preregistered package effect under the stated cost/measurement construct. It does not automatically establish physical-compute parity, contamination absence, or pure gating. |
| **ROBUSTNESS/SENSITIVITY** | Prespecified stress tests after or alongside the primary result, without replacing the primary analysis. | The robustness plan itself must be frozen before inspecting the corresponding result when it could affect interpretation. | How sensitive the primary conclusion is to semantic contamination, prompt variants, hosted nondeterminism, hidden/physical compute uncertainty, benchmark-fidelity corrections, alternate family groupings, serving interference, or other residual assumptions. Failure narrows interpretation; it must not be hidden by selecting a favorable robustness run. |

`PASS`, `FAIL`, and `INCOMPLETE` are empirical evaluator outcomes, **not workflow stages**. A dry or
pilot run may be complete and mechanically successful while still having zero scientific credit.

A methodological precedent for this separation is the CONSORT extension for pilot/feasibility
trials, which distinguishes feasibility objectives from a later definitive effectiveness test and
warns against treating an underpowered pilot as the definitive hypothesis test:
<https://www.bmj.com/content/355/bmj.i5239>. **INFERENCE for Supernova:** this source does not govern
software experiments, but it supports the general design discipline of separating “can the protocol
run?” from “did the treatment effect pass its confirmatory test?”

## Hosted and scheduled-chat cost construct

**EVIDENCE — repository.** G1-107 explicitly targets ChatGPT scheduled-task execution where provider
token telemetry is unavailable. The merged cost model already states that `cost_model_frozen`
remains false until the measurement environment and comparison assumptions are frozen.

**EVIDENCE — direct external source.** OpenAI's token-usage documentation distinguishes input,
output, cached-input and reasoning tokens and says API responses can expose usage fields; it also
notes that reasoning tokens can be invisible in the answer text and that interrupted streaming can
leave usage unavailable:
<https://help.openai.com/en/articles/4936856> and
<https://help.openai.com/en/articles/10478918-reviewing-api-usage-and-costs>.

**INFERENCE.** API telemetry and scheduled-chat observability are different measurement surfaces.
If the experimental runtime does not expose provider usage counters, Supernova must not fabricate
or relabel a local estimate as provider-metered tokens, and it must not describe the result as
physical-compute equality.

### Frozen proxy choices

Before confirmatory execution, choose and name exactly one hosted cost construct:

1. **Provider-metered hosted proxy** — when raw provider usage is available, record model-call count,
   provider-reported input/output token usage (including cached/reasoning detail when surfaced),
   verifier elapsed time, and orchestration elapsed time. Missing required provider usage is typed
   unknown, not zero.
2. **Scheduled-chat observable proxy** — when provider token telemetry is unavailable, record every
   model dispatch, exact UTF-8 byte counts of the canonical rendered model-visible input and visible
   output, optional token estimates from one frozen tokenizer/version labelled explicitly as
   estimates, verifier elapsed time, orchestration elapsed time, tool-call identities/results that
   enter later prompts, retries/failures, and trusted dispatch times. This is an observable proxy,
   not a provider-token or physical-compute meter.

The same proxy definition, accounting boundary and missing-data rules apply to all arms. A shared
maximum ceiling is not the same as equal realized cost; either match realized proxy vectors, allow a
prospectively frozen residual-budget policy, or report a performance-versus-proxy-cost frontier.

### Prospective sensitivity analysis for hidden compute

For hosted confirmation where physical compute and hidden reasoning are not independently auditable,
freeze a sensitivity analysis **before outcomes are inspected**. At minimum:

- vary arm-specific hidden model work over a stated multiplicative/additive range relative to the
  observable/provider-metered proxy;
- separately stress cached-input advantage, request-shape/prefill effects, failed-call hidden work,
  verifier CPU/resource intensity, and shared serving delays when relevant;
- recompute which arm would be considered cheaper/equal/more expensive under each bound;
- report the smallest hidden-cost asymmetry that would overturn the fairness interpretation; and
- if plausible bounds overturn the conclusion, narrow the claim to superiority under the observed
  proxy rather than “equal compute.”

No sensitivity bound can prove true hardware equality; it makes the residual assumption explicit.

## Composable evidence bindings — do not build one mega-certificate

The following are separate contracts with separate failure modes. They should join through immutable
subject IDs/digests, not be collapsed into a single oversized certificate:

1. **Benchmark/input binding** — benchmark lock root, split contract, problem-content digest,
   preprocessing/formalization identity, and canonical common prompt/input digest.
2. **Runtime/toolchain binding** — resolved verifier executable/image, Lean/toolchain and proof
   library state, cwd, allowlisted environment, model/provider/version and other runtime identity.
3. **Outcome/verifier binding** — exact challenge/problem subject, final proof/artifact digest,
   final-verifier receipt/policy, and independently checkable PASS evidence for a counted solve.
4. **Dispatch/cost binding** — pre-dispatch cell execution identity, all model/verifier/tool/
   orchestration events, closed cost report or proxy record, and the deterministic join to the
   scientific outcome.

A confirmatory cell is admissible only when every binding required for **that claim** validates, but
one contract must not silently stand in for another. For example, a correct benchmark digest does
not attest the verifier runtime, and a valid proof receipt does not prove complete cost accounting.

## Threat ledger

The severity labels describe how damaging the threat would be to the corresponding interpretation.
The **phase** column identifies when the mitigation must be resolved for the stated claim.

| ID | Threat | Severity | Evidence and inference | Smallest falsifier / mitigation | Phase |
| --- | --- | --- | --- | --- | --- |
| T1 | Treatment-bundle confounding | CRITICAL | Verified chain combines decomposition, state, verification, continuation and consumption. A win need not be a pure gating effect. | Freeze the package estimand now; for a pure-gating claim add matched ablations that remove one mechanism at a time. | Confirmatory |
| T2 | Common ceiling != equal realized cost | CRITICAL | Test-time allocation changes capability; both arms can be below one ceiling while one spends more. Snell et al.: <https://arxiv.org/abs/2408.03314>. | Match realized proxy cost, freeze residual-budget reuse, or compare preregistered cost frontiers. | Confirmatory |
| T3 | Intermediate verifier is a search oracle | CRITICAL | Verifier ranking/feedback can improve search independently of downstream verified-object consumption. Cobbe et al.: <https://arxiv.org/abs/2110.14168>. | Match verifier query count, response vocabulary, diagnostic bandwidth and verification frequency. | Confirmatory |
| T4 | Distinct IDs != independent verification | HIGH | Different producer/verifier strings do not prove independent process/runtime state. | Bind implementation/runtime identities and replay in a clean verifier process. | Confirmatory for independence claim; otherwise robustness limitation |
| T5 | Benchmark contamination / selection leakage | CRITICAL | Public benchmarks may appear in training and adaptive benchmark choice can overfit. LiveBench: <https://arxiv.org/abs/2406.19314>. | Freeze content/split before confirmation; protect confirmatory set; report residual training-overlap risk. | Confirmatory; semantic residual risk in robustness |
| T6 | Formal-library answer leakage | HIGH | Held-out theorem statements can leak through known proofs/helper lemmas in allowed libraries. LeanDojo: <https://proceedings.neurips.cc/paper_files/paper/2023/file/4441469427094f8873d0fecb0c4e1cee-Paper-Datasets_and_Benchmarks.pdf>. | Freeze library/retrieval snapshot and audit exact/near proof overlap. | Confirmatory for allowed-access parity; residual novelty/generalization in robustness |
| T7 | Cross-arm / cross-problem state leakage | CRITICAL | Conversation state, products, caches or diagnostics can cross causal cells. | Fresh per-cell state; frozen order/randomization; prohibit undeclared transfer. | Confirmatory |
| T8 | Unequal model/tool/prompt capability | CRITICAL | Provider/model/version, prompt, reasoning effort and tools can dominate the mechanism. | Freeze common capability surface and explicit arm-specific causal delta. | Confirmatory |
| T9 | Free work outside accounting boundary | HIGH | Failed calls, tools, preprocessing, retrieval, setup and human intervention may subsidize one arm. | Predeclare boundary; dispatch-register every attributable operation; missing telemetry is unknown. | Confirmatory for included resources; omitted-resource sensitivity afterward |
| T10 | Early-stop / retry asymmetry | CRITICAL for pure gating | Adaptive retries change search opportunity. Snell et al.: <https://arxiv.org/abs/2408.03314>. | Match or explicitly include reject/retry in package estimand; charge all attempts. | Confirmatory |
| T11 | Verifier overfitting / diagnostic exploitation | HIGH | Iterative Lean feedback can be optimized against; checker acceptance may be narrower than intended construct. | Narrow search-verifier channel; clean final replay; preserve raw evidence. | Confirmatory for kernel-valid claim; broader semantic robustness later |
| T12 | Seed luck / undefined replication | HIGH | Selective reruns create researcher degrees of freedom. | Freeze seeds/replicate count and aggregation; preserve failed attempts. | Confirmatory |
| T13 | Confirmatory-set reuse | CRITICAL | Repeated adaptation to held-out outcomes converts the holdout into development data. | Separate development/pilot and one protected confirmatory analysis or valid retest rule. | Confirmatory |
| T14 | Dry-run evidence promoted to science | BLOCKER if misreported | Active `GOAL1.json` is dry and cost-unfrozen. | Label all dry/pilot outputs zero-credit. | Dry/Pilot |
| T15 | Kernel-valid but benchmark-invalid | HIGH/CRITICAL by benchmark | Formal statements can be vacuous/unsafe/wrongly formalized. Ammanamanchi et al.: <https://arxiv.org/abs/2606.29493>. | Mechanical fidelity audit and audited informal-to-formal mapping. | Core malformed-item checks before confirmation; broader sensitivity after |
| T16 | Verifier compute/frequency is a treatment | CRITICAL | Solve-versus-verify allocation and verification frequency affect performance. Setlur: <https://proceedings.mlr.press/v267/setlur25a.html>; Singhi: <https://arxiv.org/abs/2504.01005>. | Match/log verifier frequency and account generator+verifier resources jointly. | Confirmatory |
| T17 | Memorized instance vs structural reasoning | HIGH | Fresh/variabilized math can expose memorization. MathArena: <https://arxiv.org/abs/2505.23281>; VAR-MATH: <https://arxiv.org/abs/2507.12885>. | Fresh/post-cutoff or structure-preserving variants under frozen analysis. | Robustness/Sensitivity |
| T18 | Hosted model time drift | CRITICAL if arm/time confounded | Hosted model behavior/version can move. Chen et al.: <https://arxiv.org/abs/2307.09009>. | Specific version where possible; trusted request time; paired counterbalanced narrow blocks. | Confirmatory |
| T19 | Backend nondeterminism | HIGH | Batch/hardware/numerics can change outputs even at deterministic decoding. Yuan et al.: <https://proceedings.neurips.cc/paper_files/paper/2025/hash/f80094a824ba5912d4a2de169c404a40-Abstract-Conference.html>. | Freeze self-hosted runtime or randomize/replicate hosted runs under a prospective policy. | Confirmatory design control; residual sensitivity |
| T20 | Soft contamination | CRITICAL for strong OOD claim | Semantic duplicates evade exact/n-gram filters. Spiesberger et al.: <https://arxiv.org/abs/2602.12413>. | Semantic/family overlap audit or fresh post-cutoff families; state residual risk. | Robustness/Sensitivity unless clean-OOD is part of primary claim |
| T21 | Hidden-reasoning telemetry | HIGH | Invisible reasoning can be billed/used without visible text. CoIn: <https://arxiv.org/abs/2505.13778>. | Name provider-metered or scheduled-chat proxy; never zero-fill; freeze hidden-cost sensitivity. | Confirmatory proxy definition; physical-compute uncertainty in robustness |
| T22 | Verifier-conditioned retry mismatch | CRITICAL for pure gating | Product-only now matches representation/chain semantics better, but verified chain still gets FAIL-conditioned reject/retry. Leanabell-Prover-V2: <https://arxiv.org/abs/2507.08649>. | Primary estimand = verified-gated search+consumption package unless retry is matched. | Confirmatory |
| T23 | Clustered problem families break ordinary McNemar assumptions | CRITICAL if clustered | Ordinary McNemar assumes independence across matched pairs. Eliasziw & Donner: <https://onlinelibrary.wiley.com/doi/10.1002/sim.4780101211>. | Freeze independent sampling unit; family-level or cluster-aware inference. | Conditional confirmatory blocker |
| T24 | Aggregate tokens != physical compute | CRITICAL only for equal-compute claim | Sequence shape, prefill/decode and batching change work. Vaswani: <https://papers.nips.cc/paper_files/paper/2017/hash/3f5ee243547dee91fbd053c1c4a845aa-Abstract.html>; Sarathi-Serve: <https://www.usenix.org/conference/osdi24/presentation/agrawal>. | Call the metric a token/call proxy unless auditable hardware compute is available; freeze sensitivity. | Proxy definition confirmatory; physical-compute question robustness |
| T25 | Self-declared event manifest != complete provenance | CRITICAL | A harness can omit work from both expected and observed sets; telemetry may drop. OpenTelemetry SDK: <https://opentelemetry.io/docs/specs/otel/trace/sdk/>. | Trusted pre-dispatch append-only registration plus reconciliation/drop detection. | Confirmatory; pilot may exercise it without credit |
| T26 | Shared serving quota/queue interference | CRITICAL when hosted cells share capacity | Provider quotas/caches/backoff can couple arms. OpenAI rate limits: <https://platform.openai.com/docs/guides/rate-limits>; Anthropic: <https://docs.anthropic.com/en/api/rate-limits>. | Isolate serving pools or serialize/counterbalance with complete rate-limit/retry telemetry. | Conditional confirmatory blocker |
| T27 | Outcome not bound to final-verifier evidence | CRITICAL | Naked `verifier_passed`/`solved` booleans cannot prove which artifact/challenge passed. Lean validation: <https://lean-lang.org/doc/reference/latest/ValidatingProofs/>. | Bind exact challenge, artifact digest, verifier/runtime policy and PASS receipt; clean replay. | Confirmatory; pilot should exercise join |
| T28 | Scientific record not bound to benchmark bytes | CRITICAL | A locked benchmark exists separately from active scientific records. | Bind `BENCHMARK.lock.json` root, split contract and transforms into experiment/outcome identities. | Confirmatory |
| T29 | Statistical significance != meaningful effect | HIGH | P-value thresholds do not encode effect magnitude. ASA: <https://doi.org/10.1080/00031305.2016.1154108>; Lakens: <https://doi.org/10.1525/collabra.33267>. | Freeze effect quantity, meaningful margin or explicit no-margin interpretation, precision/power target. | Confirmatory |
| T30 | Paired arms not bound to exact same model-visible input | CRITICAL | Meaning-preserving prompt formatting can materially change performance. Sclar et al.: <https://openreview.net/pdf?id=RIu5lyNXjT>. | Content-address canonical common input; arm-specific instructions are a frozen causal delta. | Confirmatory |
| T31 | Repeated confirmatory looks / optional stopping | CRITICAL | Reusing fixed-horizon p-values across outcome-dependent looks inflates error. Johari et al.: <https://pubsonline.informs.org/doi/10.1287/opre.2021.2135>. | One immutable primary analysis or prospectively valid sequential/alpha-spending rule. | Confirmatory |
| T32 | Verifier identity not hermetic | CRITICAL for reproducible proof evidence | Ambient PATH/env/cwd can change the checker behind one command string. Python subprocess: <https://docs.python.org/3/library/subprocess.html>; Lean Elan: <https://lean-lang.org/doc/reference/latest/Build-Tools-and-Distribution/Managing-Toolchains-with-Elan/>. | Bind resolved executable/image, toolchain/library, cwd and allowlisted env; fail replay on drift. | Confirmatory for reproducible final evidence; deeper environment stress robustness |
| T33 | Elapsed ms != resource-normalized non-model compute | HIGH; CRITICAL only for physical-compute claim | Wall time includes queue/sleep and ignores core/accelerator intensity. Python time: <https://docs.python.org/3/library/time.html>; Linux cgroup v2: <https://kernel.org/doc/html/next/admin-guide/cgroup-v2.html>. | Freeze resource allocation; measure CPU/accelerator usage where available; otherwise call it elapsed-time proxy and sensitivity-test. | Proxy definition confirmatory; physical-compute parity robustness |
| T34 | Cost report not bound to scored scientific cell | CRITICAL | `OutcomeRecord.cost` can be supplied independently of the closed cost telemetry report. W3C Trace Context: <https://www.w3.org/TR/trace-context/>. | Pre-dispatch cell execution ID; propagate through all events/report; evaluator derives/reconciles cost and rejects replay/mismatch. | Confirmatory; pilot should test failure paths |

## Interpretation rule

Passing implementation tests is necessary but not sufficient. For every future change ask:
**could this improve the verified-gated arm without changing the intended frozen package?** If yes,
it is either a confound that needs matching or a reason to narrow the estimand.

For the first confirmatory run, the scientifically honest statement is therefore conditional:
Supernova may estimate the effect of the **verified-gated search-and-consumption package under the
frozen observable/provider-metered cost proxy**. A claim about pure intermediate-product gating,
contamination-free structural reasoning, or equal physical compute requires the additional matched
control or robustness evidence identified above.