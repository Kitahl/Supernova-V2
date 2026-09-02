# Supernova V2 status

Status date: 2026-08-28  
Repository authority: `Kitahl/Supernova-V2`  
Reviewed main: `a452cfb7c3d3e6f0e4f482bddd19c96a8942fec5`

## Decision

Goal 1 is **not evaluated**. It has neither PASS nor FAIL.

The engineering pilot is complete: the repository can execute all five declared
arms, preserve frozen visible request/response artifacts, close an append-only
dispatch/completion join, record verifier events, authenticate verified-chain
retry and product admission, and aggregate a complete-cost report in one
deterministic non-credit cohort.

That result proves the components connect. It does not establish the scientific
hypothesis because the cohort uses fixed engineering fixtures and stub verifier
callbacks rather than a frozen confirmatory benchmark, real scheduled-chat
outputs, and Lean kernel verification.

Goal 2 is not yet defined in this repository and has not started.

## Merged engineering evidence

| Capability | Merged evidence |
| --- | --- |
| Scheduled-chat artifact identity | PR #28, merge `6b4e37e1f1e9aaac2732537d2256891b724d10c7` |
| Benchmark/runtime checker | PR #34, merge `35b218a13357340611448b9ec8c0d13289b15a94` |
| Frozen execution contracts | PR #32, merge `708bc56891231928188a2413ebacf3702dd03fae` |
| Ordinary and portfolio executors | PR #41, merge `1f71a6f69dfc90e2c518f39a88a9d80f389c62b6` |
| Product-only and multi-fidelity executors | PR #43, merge `c7957b2164e920379a2298e0131d10c5078d98bb` |
| Verified-chain executor and authenticated admission | PR #45, merge `bda03978d51d6b0508f84ccbac7b2beb62d4e736` |
| Model-usage-basis enforcement | PR #30, merge `f58246539f8f4a6d9b6d757afec63f3bdbc78797` |
| Append-only dispatch and completion join | PR #23, merge `639908fd9b39cf2826c27ec34c5aa9e69ab0eb00` |
| Seeded paired manifest | PR #39, merge `d9db8a918006aa4708a43b0ee6f964b8c3a2dc59` |
| Prospective analysis plan | PR #36, merge `f9075bbf448888a87137c723293ab9afe3b3ba73` |
| Adversarial execution tests | PR #47, merge `e8648e59c095006aa7c297ec395c0801ff0c46ff` |
| Independent execution review | PR #49, merge `c9ef62234b7ddab89e926503d84a9c2f5134cb90` |
| Five-arm non-credit integration pilot | PR #51, merge `a452cfb7c3d3e6f0e4f482bddd19c96a8942fec5` |

PR #51 passed GitHub Actions run `33182555100`. Its focused integration step
passed two tests and the full job also passed the unit-test and dry-run-evaluator
steps. An independent adversarial review returned PASS at exact PR head
`e8813309716943bb89b1e15f24b5f79009bf533f`.

## What the pilot exercised

The non-credit cohort contains eight closed completions:

- ordinary: one final attempt;
- portfolio: one final attempt;
- product-only: one unverified product followed by one product-visible retry;
- multi-fidelity: one full-fidelity attempt;
- verified-chain: one failed product, one verified and admitted replacement, and
  one final answer that receives the admitted product.

The report contains all five arms, per-arm cost totals, seven verifier receipts,
a verified closed dispatch join, and authenticated verified-chain retry and
admission evidence. It emits:

- `classification = NON_CREDIT_PILOT`;
- `scientific_claim = NONE`;
- `goal1_result = NOT_EVALUATED`.

## Open scientific blockers

1. **Active protocol is not frozen.** `goal1/GOAL1.json` remains `DRY_RUN`;
   its benchmark is `UNSELECTED/UNPINNED/UNLOCKED`.
2. **Complete-cost basis is not frozen.** `cost_model_frozen` is false and
   `model_usage_basis` is `UNFROZEN`.
3. **Evidence cannot yet enter the evaluator safely.** There is no typed path
   from a verified `CompletionJoin`, verifier receipts, execution ledger, and
   `CompleteCostReport` to evaluator outcomes. Caller-supplied solved/cost
   fields are therefore not confirmatory evidence.
4. **No scientific cohort exists.** No paired five-arm run has used the frozen
   miniF2F bytes, actual scheduled-chat outputs, and Lean 4.33.1 kernel results.
5. **Control retry provenance is weaker.** Product-only and multi-fidelity
   retries identify only an earlier attempt number; verified-chain retries bind
   a signed predecessor completion and trusted execution ledger.
6. **The benchmark lock is not the active experiment.**
   `goal1/BENCHMARK.lock.json` is content-addressed and locked, but the active
   experiment has not adopted its identity, split policy, and held-out rules.
7. **No confirmatory result exists.** The required paired contrasts, complete
   cohort checks, Holm-corrected exact tests, and complete-cost comparison have
   not run.

## Next executable transition

Replace the completed pilot board with one dependency-aware confirmatory tranche:

1. freeze benchmark/split and contamination exclusions;
2. freeze runtime/verifier identity;
3. freeze ordinary, portfolio, product-only, multi-fidelity, and verified-chain
   contracts;
4. freeze the complete-cost policy and one shared model-usage basis;
5. seal one immutable confirmatory protocol digest;
6. generate a one-shot paired cohort manifest;
7. implement the typed evidence-to-evaluator bridge;
8. adversarially reject stale, partial, leaked, or unaccounted cohorts;
9. independently review the exact frozen protocol;
10. run one cohort and report only PASS, FAIL, BLOCKED, or INCOMPLETE.

In the same board transition, define Goal 2 as a contract-only ticket. Goal 2
execution must remain blocked if that contract requires a valid Goal 1 PASS.

The prior MM06, MF06, and BIL00 artifacts are archived with exact merge
evidence. Their new tickets are initially `WAITING` because dependencies are
unresolved; each becomes `READY` when its dependencies are `DONE`. Their
review-only, integration-only, and status-only authority restrictions remain.

## 2026-09-01 Goal-1 activation handoff

Scientific state remains **NOT EVALUATED** with **zero countable attempts**.
No model call occurred in the latest validation smoke.

Current activation branch is `work/PM/G1-147` at `97c44e3`; PR #96 targets
`main`. The two-phase verifier independently qualified and published in GitHub
Actions run `33498934176` from source `4383a104`. Its immutable identity is:

- image: `ghcr.io/kitahl/supernova-goal1-verifier@sha256:dc7e5754612925b8d0de1482fc87ba13b39370c7965f8286912587f030efddc5`;
- runtime lock root: `45810b9ad1046511f5ee9b1562bcb5542ee4c53c49fb4aca02ad38b82d4ebfe2`;
- Lake manifest hash: `11bc0ecb997ccd02526acc604d6da03ad3434c575d1746d3d3020780550d196b`.

PR run `33501251455` passed executor build, exact networkless preflight, locked
miniF2F validation preparation, and immutable-verifier qualification. It then
stopped in the preregistered synthetic gates before either Kimina model call:
the signed prose rejection took 7,217 ms, exceeding the frozen 5,000 ms wall
limit. Failure evidence was uploaded as artifact `9797961730` with ZIP SHA-256
`f87cd45bce31f451bb6b439352802edd7f3731ac9ba444412a9e043a4a3f1753`.

The performance observations are now three bounded failures on fresh GitHub
runners:

1. original serial runtime inventory validation: 6,616 ms (run `33491424376`);
2. two-worker inventory hashing: 7,556 ms (run `33497954822`);
3. serial `hashlib.file_digest` with one-open/fstat validation: 7,217 ms (run
   `33501251455`).

The common cost is intentional re-hashing of 8,711 runtime artifacts totaling
2,475,986,864 bytes in every fresh verifier container. The evidence rejects the
hypothesis that Python hash-loop overhead or two-worker scheduling can bring
that complete check below five seconds on the current runner. It does **not**
show that Lean proof checking takes this long, and it does not invalidate the
verifier's security qualification.

No further repair should be attempted without choosing and recording one of
these protocol/architecture decisions before any model output exists:

1. amend the non-scientific prose wall gate prospectively to a measured bound
   with margin (for example 10 seconds), retaining the 600-second Lean timeout
   and every runtime hash; or
2. specify a separately identified trusted lexical pre-classifier that can
   reject obvious non-Lean prose before the full Lean runtime is launched,
   while all candidate-shaped Lean continues through full runtime attestation
   and the hostile two-phase sandbox.

Do not silently move the five-second threshold, remove runtime artifacts from
the lock, count any failed smoke as scientific evidence, run the 20-attempt
pilot, or merge PR #96 while this decision is unresolved.

## 2026-09-01 timing-policy correction

The five-second prose criterion above was not an authorized scientific or
security requirement. It is removed prospectively before any model call. The
three observed prose-path measurements remain evidence: 6,616 ms, 7,556 ms,
and 7,217 ms (mean 7,129.67 ms). They measure the complete signed verifier path,
including a fresh-container integrity scan of 8,711 artifacts / 2,475,986,864
bytes; they are not Lean elaboration averages.

The non-credit two-attempt validation demo now uses a 60-second outer liveness
watchdog instead of inheriting the frozen 600-second confirmatory ceiling. The
watchdog is the next whole-minute boundary above the sum of the observed local
attestation maximum (7.556 s) and the current Lean default-heartbeat exhaustion
reference maximum (35.590 s, leanprover/lean4#14704). A watchdog hit maps to
UNKNOWN and cannot become INVALID. Each signed verifier record now exposes
elapsed milliseconds for every sandbox phase, allowing the demo report to
separate container/attestation, elaboration/export, and independent checking.

This amendment does not mutate the frozen confirmatory authority and does not
create scientific credit. Goal 1 remains NOT EVALUATED with zero countable
attempts until the validation ladder passes and a new confirmatory design is
sealed prospectively.

## 2026-09-02 — Two-attempt demo exposed capped reasoning, not a Lean timeout

- Workflow run 33585357319 completed both real Kimina model calls in 66590 ms
  and 68567 ms (mean 67578.5 ms) and then obtained signed INVALID
  verdicts in 7572 ms and 7041 ms.
- Both model responses reached the exact 1024-token generation cap while still
  emitting reasoning/Markdown rather than the requested tactic body. Treating
  that truncated content as an answer was an executor-boundary defect.
- The corrective non-credit configuration uses explicit DeepSeek reasoning
  separation, requires finish_reason=stop, raises the output cap to 4096,
  and uses a 300 s model-generation watchdog derived from the slower observed
  token rate. The independent Lean-verifier watchdog remains 60 s.
- These are validation-only observations. Countable Goal-1 attempts remain zero.
