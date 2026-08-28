# Goal 2 contract: verified improver of improver

Status: **definition only; no execution or scientific credit**.

Goal 2 asks a prospective question: after Goal 1 has produced an authenticated final
`PASS`, can an improved improver produce a better solver descendant than both the
untouched improver and the untouched solver under one matched complete R&D budget?

This file explains the machine-readable contract in `goal2/GOAL2.json`. The JSON is
the contract authority. This document cannot open the gate or adjudicate a result.

## Three separated components

- `F` is the pristine solver parent supplied identically to both improvement arms.
- `I0` with `M0` is the frozen control improver and its isolated memory.
- `I1` with `M1` is the treatment improver and its isolated memory.

The frozen source manifest binds F, I0, and M0 before dispatch. Post-run signed
lineage binds both IDs and exact artifact digests for I0, M0, I1, and M1, plus the
selected descendants. Component IDs, artifact digests, runtime/schema digests,
memory namespaces, arm lineage records, and selected descendant digests must be
authority-bound. Aliasing,
mixing lineages, reusing `M0` as `M1`, or substituting a descendant blocks the run.
The control and treatment descendants must differ from the pristine parent and from
one another.

## Opening gate

The checked-in contract is deliberately `CONTRACT_ONLY`; therefore it always
returns `BLOCKED`. A later pull request may freeze a complete contract, but it
cannot merely change a Boolean.

Opening requires all of the following:

1. contract phase `FROZEN`;
2. an authority-authenticated Goal 1 final-PASS receipt;
3. a single Goal 1 run ID binding exact SHA-256 digests for the final report,
   protocol, cohort, evidence bridge, and evaluator;
4. a frozen authority key identity and public key digest, while the secret remains
   host-owned and absent from the repository;
5. frozen components, cost policy, selection rule, fresh-evaluation manifest, effect
   target, sampling unit, clustering rule, and analysis-plan digest.

A missing, malformed, forged, cross-run, or substituted field returns `BLOCKED`.
The pre-dispatch gate uses only evidence available before execution: the authenticated
Goal 1 PASS, frozen contract artifacts, distinct authority commitments, and the
content-addressed F/I0/M0 source manifest. It does not require post-run descendants
or outcomes. No Goal 2 execution ticket may open while this gate is blocked.

A separate post-run evidence-admission gate validates generated I1/M1 artifacts,
lineage, cost ledgers, selections, and held-out evaluation. Opening pre-dispatch does
not imply that post-run evidence is admissible.

## Matched complete R&D cost

Both arms use the same frozen ceiling and allocation. The observable model-usage
basis is `visible_utf8_bytes`, not provider tokens and not physical compute. The
required dimensions are:

- model calls;
- input UTF-8 bytes;
- output UTF-8 bytes;
- verifier milliseconds;
- orchestration milliseconds.

An authority-authenticated freeze receipt binds the budget ID and manifest, identical
per-dimension ceilings, globally unique typed expected events across both arms, the
selection rule, candidate-set manifests, fresh-evaluation manifest, and analysis plan.
Every signed observed event ledger must reconcile exactly, including failed attempts,
retries, selection
work, and meta-improvement work. A scheduled-chat event requires exactly one model
call, positive signed UTF-8 byte measurements, and bound request/terminal-response
artifact digests. A deterministic selection may record zero model calls and bytes,
but requires command/result artifacts and positive orchestration time. Unknown usage
is never imputed as zero. Missing,
extra, malformed, replaced, or over-budget evidence blocks scientific credit.

## Selection before fresh evaluation

Each arm registers its candidate set and uses the same frozen selection rule.
Authenticated selection ledgers seal exactly one descendant per arm. The fresh
held-out evaluation manifest is content-addressed, disjoint from all R&D and
meta-improvement data, and released only after both selection seals. The evaluator has a separately frozen authority key commitment, distinct from the
R&D authority. It applies the same items, runtime, verifier, scoring, and analysis
to the control descendant, treatment descendant, and untouched solver.

The executable contract test consumes and authenticates the lineage, cost, selection,
freeze, and evaluation-release records; these are not self-asserted booleans.
Post-release candidate substitution, rule changes, early release, leakage, or a
missing untouched-solver result returns `BLOCKED`.

## Frozen effect and terminal decisions

The metric, direction, numeric margins against control and untouched `F`, sampling
unit, clustering rule, and analysis plan are frozen before opening. A caller cannot
supply or reduce a margin at adjudication time.

Terminal priority is `BLOCKED`, then `INCOMPLETE`, then `PASS` or `FAIL`:

- **BLOCKED**: an integrity, identity, freeze, cost, lineage, selection, freshness,
  or authentication precondition is absent or invalid. No scientific credit.
- **INCOMPLETE**: the gate opened and integrity remains valid, but a required sealed
  outcome is pending. No scientific credit.
- **PASS**: complete admissible evidence shows the treatment exceeds both the
  control descendant and untouched `F` by the frozen margins in the frozen
  direction.
- **FAIL**: complete admissible evidence exists but either superiority criterion is
  missed. This is a valid negative scientific result.

The contract does not claim that Goal 2 works. It specifies what future evidence
would be capable of showing that it works or does not work.
