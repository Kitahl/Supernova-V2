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
Event IDs are unique within an arm trace and, for a closed five-arm report, globally
unique across all five traces.

## Telemetry coverage invariant

`accounting_complete=true` is only a stream-close marker. It is **not evidence** that
telemetry is complete and cannot by itself close a `CompleteCostReport`.

Every arm trace must also carry a non-empty expected-event manifest. Each manifest entry
binds an event ID to its required event kind (`model_call`, `verifier`, or
`orchestration`). Report closure deterministically requires the observed event-ID/kind
mapping to equal that manifest exactly:

- every expected event must have one observed telemetry event of the same kind;
- no unexpected event may appear;
- expected event IDs and observed event IDs must each be unique within an arm;
- an observed event ID may appear in only one arm of a closed five-arm report, so one
  dispatch/completion record cannot be replayed as execution evidence for several arms;
- a manifest with zero expected events is invalid;
- because a closed Goal-1 arm trace denotes an executed solver arm, the manifest must
  contain at least one expected `model_call` attempt. An orchestration-only bookkeeping
  sentinel cannot stand in for execution, and a skipped/not-run arm is incomplete rather
  than a complete zero-cost arm.

The final bullet is a Goal-1 execution invariant, not a generic claim that every future
cost-accounting application must call a model. If a later protocol deliberately permits a
zero-model-call arm, that must be represented prospectively with a different execution
contract rather than silently weakening this closure rule after observing results.

Report-wide event-ID uniqueness closes a deterministic replay alias in the accounting API:
without it, five individually valid traces could all cite the same `event_id` and one
underlying dispatch/completion identity could appear to satisfy every arm. This invariant
is necessary but not sufficient for trusted provenance. A dishonest or incorrectly
instrumented harness can still mint distinct IDs for omitted/fabricated work, so global ID
uniqueness does **not** replace the trusted pre-execution dispatch ledger required before
scientific freeze.

Closure is snapshot-based and non-polymorphic. `ArmCostTrace` snapshots both the supplied
collections and each concrete `CostEvent` / `ExpectedCostEvent` value into fresh base-class
records. `CompleteCostReport` likewise rebuilds each concrete `ArmCostTrace` into a fresh
snapshot before validating closure. Subclasses of those accounting record types are
rejected rather than trusted through `isinstance`, because a subclass can override
aggregation or completeness properties after satisfying the base constructor. This is a
deterministic value boundary: caller-owned list aliases, event aliases, manifest-entry
aliases, or trace aliases cannot later erase or reduce accounting already captured by the
report.

Python `dataclass(frozen=True)` is not treated as proof of true immutability. It blocks
ordinary field assignment, but Python itself documents frozen dataclasses as emulated
immutability rather than a mechanism for creating truly immutable objects. The accounting
module therefore takes fresh concrete value snapshots instead of retaining caller-owned
record objects. This is defensive API isolation, not a claim that hostile code with direct
access to the report object cannot deliberately violate Python's object model.

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

Every present numeric measurement must also be an **exact built-in `int`**, not merely an
object for which `isinstance(value, int)` is true. Python permits subclasses of `int` to
override rich-comparison behavior; trusting `value < 0` on such an object would let the
object participate in its own validation. The module therefore rejects `bool`, `IntEnum`,
and custom `int` subclasses at event and externally supplied `CompleteCost` boundaries,
then snapshots only already-validated built-in integers. This is part of the same
non-polymorphic accounting boundary as the record snapshots above.

`0` is reserved for an actually observed zero. A caller must not convert unavailable
provider usage or missing timing telemetry to zero. This matters especially for failed
or retried work: the issued attempt must remain in the expected-event manifest, and if
its required resource measurement cannot be recovered, the arm remains incomplete
rather than receiving a free zero-cost attempt.

Therefore an empty trace for an executed arm cannot be asserted complete and silently
converted to exact zero cost. The same is true for a synthetic orchestration-only
sentinel: it is rejected because it does not identify any expected model attempt. Every
closed Goal-1 arm therefore has `model_calls >= 1`. Individual token or elapsed-time
dimensions may still be zero when zero was actually observed, but an executed arm cannot
produce the all-zero `CompleteCost` vector.

The execution harness must construct the expected-event manifest from the planned or
issued operations rather than infer it from telemetry after the fact. For dynamic
retries, the retry event must be registered in the manifest at dispatch, before its
telemetry is collected. The current cost module can deterministically verify manifest
coverage, reject cross-arm replay of one event identity, reject orchestration-only
pseudo-execution, and reject typed unknown measurements; it cannot by itself prove that
an external harness preregistered the manifest at the correct time or that a harness did
not fabricate distinct event IDs, fabricate an observed zero, or omit an issued retry
from the manifest. That provenance requirement must be frozen and enforced in the
experiment runtime before scientific use.

A `CompleteCostReport` closes only when **exactly all five arms** are present and every
arm satisfies the close marker, exact manifest coverage, report-wide event-ID uniqueness,
the executed-arm model-attempt invariant, and complete resource measurements:
`ordinary`, `portfolio`, `product_only`, `multi_fidelity`, and `verified_chain`.

## Fair-budget rule: componentwise, not weighted

The same frozen `CompleteCost` ceiling is applied to every arm, component by component.
An arm is within budget only if all five observed components are no greater than their
corresponding ceilings.

Budget checks and componentwise cost comparisons do not trust a caller-supplied
`CompleteCost` object merely because its fields are type-annotated. Before use, the cost
module requires an exact concrete `CompleteCost`, revalidates every dimension as an exact
built-in non-negative integer (rejecting booleans and integer subclasses), and snapshots
the five values once. The same snapshotted ceiling is then used for every arm in a report.
This closes runtime API aliases that would otherwise violate the common-budget invariant:
Python does not enforce type annotations at runtime, so a directly constructed or
post-construction-mutated `CompleteCost` could contain negative, boolean, subclassed, or
non-integer dimensions; and a `CompleteCost` subclass could override `as_tuple()` so the
apparent ceiling or comparison vector differs from its stored fields or even changes
between calls. The validated snapshot boundary prevents those objects from participating
in budget or Pareto decisions.

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
  freezes or dispatch-registers expected events, how event IDs are made unique across
  the paired five-arm execution, how dropped telemetry is detected, and how unavailable
  quantities are represented as typed unknowns rather than zero. If the provider/runtime
  cannot report a required quantity, that arm's accounting remains incomplete;
- execution semantics for skipped/no-op arms. The present Goal-1 contract treats every
  closed arm as an executed solver arm with at least one issued model call. A skipped arm
  is not a valid complete-cost observation and must remain incomplete/missing.

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
    event_id = f"{arm.value}-attempt"
    events = (CostEvent.model_call(event_id, input_tokens=0, output_tokens=0),)
    expected = (ExpectedCostEvent.model_call(event_id),)

    if arm is Arm.ORDINARY:
        events = (
            CostEvent.model_call("ordinary-call-1", input_tokens=1200, output_tokens=300),
            CostEvent.verifier("ordinary-verify-1", milliseconds=42),
            CostEvent.orchestration("ordinary-driver-1", milliseconds=18),
        )
        expected = (
            ExpectedCostEvent.model_call("ordinary-call-1"),
            ExpectedCostEvent.verifier("ordinary-verify-1"),
            ExpectedCostEvent.orchestration("ordinary-driver-1"),
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

The zero-token model events in the example are explicit model-attempt coverage with known
measurements; they contribute one `model_calls` unit and therefore do not create a
complete all-zero arm. The example demonstrates the accounting API only. It is not an
instruction to freeze the bootstrap ceiling or any scalar exchange rate.
