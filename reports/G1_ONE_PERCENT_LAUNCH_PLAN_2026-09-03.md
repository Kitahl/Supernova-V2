# Goal 1 one-percent validation launch plan — 2026-09-03

## Classification

- Stage: **NON_CREDIT_VALIDATION_ONLY**
- Scientific credit: **NONE**
- Countable attempts before and after: **0**
- Scale: **20 model attempts** = 5 frozen validation problems × 2 arms ×
  2 attempts per problem-arm
- Terminal action: **STOP_AND_ANALYZE_BEFORE_ANY_LARGER_CALIBRATION**

## Evidence carried forward

GitHub Actions run 33834392060 completed two fresh, networkless Kimina model
calls and obtained signed verifier evidence for both answers. Neither model
answer was Lean-valid. There was no model timeout, verifier timeout, transcript
capture failure, or benchmark-syntax failure.

The old smoke gate combined execution integrity with a stochastic requirement
for at least one success in two attempts. The user explicitly authorized this
20-attempt non-credit transition after reviewing that result. The transition is
recorded prospectively in integration/goal1_validation_pilot/PILOT_PLAN.json.

## Meditate / Gauntlet decision

- Default pull named: add more prerequisites after a failed gate.
- Goal-state: measure whether the actual verified-chain treatment is expressible
  with the pinned model before any larger calibration or scientific launch.
- One load-bearing unknown: product emission, admission, and usable later
  exposure under the actual product-chain prompt.
- Sunk-cost cut: if prior scaffolding were discarded, the smallest useful next
  experiment would still be this exact 20-attempt validation pilot.
- Release decision: run only the five-by-two-by-two pilot and stop.

## Execution contract

1. Select the same five validation problems using the already-frozen seed.
2. Run attempts round-robin by attempt, then frozen problem order.
3. Alternate arm order by problem-plus-attempt parity.
4. Ordinary gets two independent fresh-context baseline prompts per problem.
5. Verified-chain gets the frozen product prompt. A Lean-PASS product from
   attempt 0 is visible to attempt 1 for that problem only.
6. Never reinterpret an unmarked product-arm response as a final answer.
7. Malformed output is a typed failed attempt, not a run-level crash.
8. Every answered response, including malformed output, goes through the
   isolated verifier and receives signed evidence.
9. Report ordinary and chain best-of-two success separately from product
   emission, product admission-given-emission, and later usable exposure.
10. Do not infer an effect or choose confirmatory sample size from this pilot.

## Launch integrity gate

The pilot is mechanically complete only if:

- exactly 20 typed attempts are present;
- every answered attempt has a signed verifier record;
- the benchmark is the locked validation split;
- the verifier is resolved from the previously qualified candidate tag to an
  immutable registry digest, then re-qualified before model dispatch;
- the report says scientific credit NONE and countable attempts 0.

Zero model successes does not make the execution incomplete. It is a capability
result to analyze after the stop.
