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
