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
Event IDs are unique within an arm trace.

## Telemetry coverage invariant

`accounting_complete=true` is only a stream-close marker. It is **not evidence** that
telemetry is complete and cannot by itself close a `CompleteCostReport`.

Every arm trace must also carry a non-empty expected-event manifest. Each manifest entry
binds an event ID to its required event kind (`model_call`, `verifier`, or
`orchestration`). Report closure deterministically requires the observed event-ID/kind
mapping to equal that manifest exactly:

- every expected event must have one observed telemetry event of the same kind;
- no unexpected event may appear;
- expected event IDs and observed event IDs must each be unique;
- a manifest with zero expected events is invalid.

Closure is snapshot-based. `ArmCostTrace` copies the supplied telemetry and expected-event
collections into immutable tuples, and `CompleteCostReport` likewise snapshots its trace
collection. This applies even when callers use the public dataclass constructors directly
rather than the convenience constructors. Mutating a caller-owned list after report
closure therefore cannot delete an expensive event, erase its expected-manifest entry,
or remove an arm from a previously accepted report. `frozen=True` is not treated as a
substitute for this copy because freezing a dataclass does not deep-freeze mutable objects
held in its fields.

Identity coverage is necessary but not sufficient. Every cost-bearing measurement on an
observed event must also be known. `None` is the typed representation of unavailable
telemetry and prevents report closure:

- a model-call event is incomplete if either input-token or output-token usage is
  unknown;
- a verifier event is incomplete if its elapsed milliseconds are unknown;
- an orchestration event is incomplete if its elapsed milliseconds are unknown.

Relevant measurement fields default to `None`, not `0`, at the `CostEvent` API boundary.
Constructing an event without supplying its required token or elapsed-time measurement
therefore produces typed unknown telemetry and cannot close a report. An explicit zero
must be supplied to assert an observed zero. Fields that are irrelevant to an event kind
may remain `None`; a nonzero cross-category value is rejected.

`0` is reserved for an actually observed zero. A caller must not convert unavailable
provider usage or missing timing telemetry to zero. This matters especially for failed
or retried work: the issued attempt must remain in the expected-event manifest, and if
its required resource measurement cannot be recovered, the arm remains incomplete
rather than receiving a free zero-cost attempt.

Therefore an empty trace for an executed arm cannot be asserted complete and silently
converted to exact zero cost. If five callers submit empty traces with
`accounting_complete=true`, report closure fails because the expected telemetry is
missing. A measured zero is still representable, but it needs both coverage evidence
and known measurements: for example, an expected orchestration event observed with
`milliseconds=0` contributes zero while proving that the accounting event was actually
present.

The execution harness must construct the expected-event manifest from the planned or
issued operations rather than infer it from telemetry after the fact. For dynamic
retries, the retry event must be registered in the manifest at dispatch, before its
telemetry is collected. The current cost module can deterministically verify manifest
coverage and reject typed unknown measurements; it cannot by itself prove that an
external harness preregistered the manifest at the correct time or that a harness did
not fabricate an observed zero. That provenance requirement must be frozen and enforced
in the experiment runtime before scientific use.

A `CompleteCostReport` closes only when **exactly all five arms** are present and every
arm satisfies the close marker, exact manifest coverage, and complete resource
measurements: `ordinary`, `portfolio`, `product_only`, `multi_fidelity`, and
`verified_chain`.

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
- telemetry completeness rules, including how the execution harness constructs and
  freezes or dispatch-registers expected events, how dropped telemetry is detected, and
  how unavailable quantities are represented as typed unknowns rather than zero. If the
  provider/runtime cannot report a required quantity, that arm's accounting remains
  incomplete.

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
from supernova_goal1.cost import (
    ArmCostTrace,
    CompleteCostReport,
    CostEvent,
    ExpectedCostEvent,
)

traces = []
for arm in Arm:
    event_id = f"{arm.value}-driver"
    events = (CostEvent.orchestration(event_id, milliseconds=0),)
    expected = (ExpectedCostEvent.orchestration(event_id),)

    if arm is Arm.ORDINARY:
        events = (
            CostEvent.model_call("call-1", input_tokens=1200, output_tokens=300),
            CostEvent.verifier("verify-1", milliseconds=42),
            CostEvent.orchestration("driver-1", milliseconds=18),
        )
        expected = (
            ExpectedCostEvent.model_call("call-1"),
            ExpectedCostEvent.verifier("verify-1"),
            ExpectedCostEvent.orchestration("driver-1"),
        )

    traces.append(
        ArmCostTrace.from_events(
            arm,
            events,
            expected_events=expected,
            accounting_complete=True,
        )
    )

report = CompleteCostReport.from_traces(traces)
ceiling = CompleteCost(16, 200_000, 100_000, 600_000, 600_000)
assert report.within_budget(ceiling)[Arm.ORDINARY]
```

The zero-valued orchestration events in the example are explicit coverage evidence with
known measurements, not permission to use an empty trace or to substitute zero for
unknown telemetry. The example demonstrates the accounting API only. It is not an
instruction to freeze the bootstrap ceiling or any scalar exchange rate.
