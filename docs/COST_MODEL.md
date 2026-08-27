# Goal 1 complete-cost accounting

This document defines the accounting proposal implemented by `supernova_goal1.cost`.
It does **not** freeze `goal1/GOAL1.json.cost_model_frozen`; that flag should remain
false until the experiment owner has frozen the measurement environment and justified
all comparison assumptions below.

## Cost vector

Every arm is accounted in the existing five-dimensional `CompleteCost` vector:

1. `model_calls` — every attempted model invocation, including failed/retried calls for
   which a request was actually issued;
2. `input_tokens` — all provider-reported input tokens for those calls;
3. `output_tokens` — all provider-reported output tokens for those calls;
4. `verifier_milliseconds` — cumulative elapsed milliseconds spent in every verifier
   invocation, including failures, timeouts, and retries;
5. `orchestration_milliseconds` — cumulative elapsed milliseconds spent in non-model,
   non-verifier orchestration work attributable to the arm.

The accounting unit is an event. Model-call, verifier, and orchestration events have
separate shapes so the same quantity cannot be silently charged to two categories.
Event IDs are unique within an arm trace. Every arm trace must be explicitly closed
with `accounting_complete=true`; an unknown or missing telemetry source must not be
converted to zero. A zero-event closed trace therefore means measured zero, not
"missing".

A `CompleteCostReport` closes only when **exactly all five arms** are present:
`ordinary`, `portfolio`, `product_only`, `multi_fidelity`, and `verified_chain`.

## Fair-budget rule: componentwise, not weighted

The same frozen `CompleteCost` ceiling is applied to every arm, component by component.
An arm is within budget only if all five observed components are no greater than their
corresponding ceilings.

The implementation deliberately defines **no scalar weighted total**. There is no
scientific basis in this repository for deciding, for example, that one verifier
millisecond equals some fixed number of tokens or that one model call equals a fixed
amount of orchestration time. `compare_complete_cost` therefore returns a partial-order
relation: equal, left-dominates, right-dominates, or incomparable. Costs that trade one
resource for another remain incomparable unless a future protocol supplies externally
justified, preregistered conversion factors.

This refusal is intentional: freezing arbitrary weights after observing arm behavior
would create a researcher degree of freedom capable of changing the winner.

## Measurement assumptions that must be frozen before scientific use

The five components are comparable across arms only under a common measurement
environment. At minimum, the experimental protocol must freeze and record:

- the model/provider/model-version and tokenizer used by all arms, or a justified
  preregistered conversion rule for heterogeneous models;
- token-accounting semantics, including how cached/reused input tokens are reported;
- the verifier implementation, host class, timeout policy, and timing method;
- the orchestration runtime/host class and the boundary between verifier and
  orchestration work;
- whether external tool calls are allowed and, if so, which category owns their cost;
- retry/cancellation rules and what constitutes an issued model call;
- the rule for shared setup work. Any setup needed by an arm must be charged to that
  arm; shared infrastructure must not subsidize one treatment selectively;
- concurrency semantics. Verifier and orchestration durations are cumulative
  per-operation elapsed time, so overlapping portfolio work is summed rather than
  receiving a free makespan discount;
- telemetry completeness rules. If the provider/runtime cannot report a required
  quantity, that arm's accounting remains incomplete rather than substituting zero.

Under heterogeneous models, hardware, verifier implementations, or tool access, the raw
vector can still be reported, but cross-arm fairness is not established merely by this
module.

## Known exclusions

The current repository contract does not include energy, storage, network bytes,
provider currency price, model-call latency, or hardware depreciation as separate cost
dimensions. Those omissions are assumptions of the present Goal 1 contract, not facts
that they are scientifically irrelevant. If any omitted quantity can materially differ
between arms, the cost model should remain unfrozen until the contract is amended or a
prospectively justified normalization is adopted.

## Example

```python
from supernova_goal1.contracts import Arm, CompleteCost
from supernova_goal1.cost import ArmCostTrace, CompleteCostReport, CostEvent

traces = []
for arm in Arm:
    events = ()
    if arm is Arm.ORDINARY:
        events = (
            CostEvent.model_call("call-1", input_tokens=1200, output_tokens=300),
            CostEvent.verifier("verify-1", milliseconds=42),
            CostEvent.orchestration("driver-1", milliseconds=18),
        )
    traces.append(
        ArmCostTrace.from_events(arm, events, accounting_complete=True)
    )

report = CompleteCostReport.from_traces(traces)
ceiling = CompleteCost(16, 200_000, 100_000, 600_000, 600_000)
assert report.within_budget(ceiling)[Arm.ORDINARY]
```

The example demonstrates the accounting API only. It is not an instruction to freeze
the bootstrap ceiling or any scalar exchange rate.
