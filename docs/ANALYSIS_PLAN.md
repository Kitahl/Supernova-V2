# Goal 1 prospective analysis plan

Ticket: `G1-110`  
Analysis contract: `goal1-confirmatory-primary-v1`  
Status: **SUPERSEDED_NON_AUTHORITY** by the sealed rules in
`goal1/CONFIRMATORY_PROTOCOL.json`. This file is retained as a historical
prospective design record and cannot open dispatch or create scientific credit.

## Scope and non-claims

This was the prospective precursor for the first confirmatory test of Goal 1.
The active authority is now `goal1/GOAL1.json`, and the exclusive scientific
rules are the content-bound `goal1/CONFIRMATORY_PROTOCOL.json`. Confirmatory
execution remains blocked because no admissible execution-authority bundle or
sealed manifest exists. Dry-run and pilot results have zero scientific credit.

The primary estimand is the package effect of verified-gated search and
consumption. It includes the verified-chain arm's frozen decomposition,
verification, reject/retry, and downstream-consumption policy. It is not a pure
verification-gating effect unless a future control matches every other part of
that policy.

This document freezes an analysis rule; it does not prove that benchmark,
runtime, dispatch, cost, or verifier evidence exists. Those facts must arrive as
separately validated, content-bound inputs. A mechanical evaluator decision by
itself is not sufficient scientific evidence.

## Machine-readable analysis contract

```json
{
  "schema_version": 1,
  "analysis_id": "goal1-confirmatory-primary-v1",
  "candidate_arm": "verified_chain",
  "control_arms": [
    "ordinary",
    "portfolio",
    "product_only",
    "multi_fidelity"
  ],
  "sampling": {
    "unit": "problem",
    "pairing_key": "problem_id",
    "required_arms_per_unit": 5,
    "replicates_per_problem_arm": 1,
    "family_independence_gate": "FREEZE_FAMILY_IDS_AND_INCLUDE_AT_MOST_ONE_PROBLEM_PER_FAMILY_OR_VERSION_A_CLUSTER_AWARE_PLAN"
  },
  "estimand": {
    "name": "verified_gated_search_and_consumption_package_effect",
    "outcome": "final_independently_kernel_verified_solve",
    "effect_per_control": "Delta_c=(W_c-L_c)/N",
    "pure_gating_claim": "FORBIDDEN_UNTIL_RETRY_AND_FEEDBACK_POLICY_ARE_MATCHED"
  },
  "cost_construct": {
    "primary": "COMMON_FROZEN_COMPLETE_COST_CEILING_WITH_SYMMETRIC_ACCOUNTING",
    "required_policy": "FREEZE_RESIDUAL_BUDGET_REUSE_AND_CHARGE_ALL_ATTEMPTS_FAILURES_VERIFIER_AND_ORCHESTRATION_EVENTS",
    "non_claim": "DOES_NOT_ESTABLISH_EQUAL_REALIZED_OR_PHYSICAL_COMPUTE"
  },
  "primary_analysis": {
    "wins": "W_c=sum_i 1[Y_i,verified_chain=1 and Y_i,c=0]",
    "losses": "L_c=sum_i 1[Y_i,verified_chain=0 and Y_i,c=1]",
    "test": "mcnemar_exact_two_sided(W_c,L_c)",
    "direction": "W_c>L_c",
    "multiplicity": "holm_step_down(four_control_p_values,familywise_alpha)",
    "alpha_source": "ExperimentSpec.familywise_alpha",
    "pass_rule": "FOR_EVERY_CONTROL: W_c>L_c AND holm_rejects_null_c",
    "effect_reporting": "REPORT_DELTA_C_AND_W_C_AND_L_C_FOR_EVERY_CONTROL_WITH_NO_SIGNIFICANCE_ONLY_INTERPRETATION"
  },
  "incomplete_runs": {
    "required_cells": "EVERY_FROZEN_PROBLEM_X_ALL_FIVE_ARMS",
    "missing_cell": "INCOMPLETE_NO_PRIMARY_HYPOTHESIS_TEST",
    "terminal_failure": "UNSOLVED_ONLY_IF_THE_FROZEN_EXECUTION_TO_OUTCOME_RULE_AND_COMPLETE_EVIDENCE_REQUIRE_IT_OTHERWISE_MISSING",
    "retry_policy": "ONLY_PREREGISTERED_SYMMETRIC_WITHIN_CELL_RETRIES_CHARGED_TO_THE_CELL",
    "post_terminal_rerun": "FORBIDDEN_FOR_THE_PRIMARY_ANALYSIS_ID",
    "replacement_problem": "FORBIDDEN_AFTER_MANIFEST_FREEZE"
  },
  "pilot": {
    "analysis_id": "goal1-discordance-pilot-v1",
    "role": "NON_CREDIT_FEASIBILITY_AND_DISCORDANCE_ESTIMATION_ONLY",
    "may_enter_confirmatory_test": false,
    "may_choose_confirmatory_items_prompts_or_arms": false,
    "permitted_power_inputs": [
      "pilot_problem_count",
      "total_discordance_D_c=W_c+L_c_for_each_control"
    ],
    "forbidden_power_inputs": [
      "pilot_win_direction",
      "pilot_p_values",
      "confirmatory_outcomes"
    ]
  },
  "power_update": {
    "familywise_target_power": 0.8,
    "effect_target_freeze": "BEFORE_PILOT_OUTCOME_INSPECTION",
    "discordance_rate": "d_c=D_c/N_pilot",
    "conditional_win_probability": "q_c=(d_c+delta_c)/(2*d_c)",
    "validity_condition": "0<delta_c<d_c<=1",
    "planning_alpha_per_contrast": "familywise_alpha/4",
    "planning_beta_per_contrast": "(1-familywise_target_power)/4",
    "per_contrast_target_power": 0.95,
    "algorithm": "FOR_EACH_CONTROL_FIND_SMALLEST_INTEGER_N_WITH_EXACT_UNCONDITIONAL_MCNEMAR_POWER_AT_LEAST_0.95_THEN_SET_N_CONFIRMATORY_TO_THE_MAXIMUM",
    "exact_power_sum": "SUM_m BinomialPMF(m;N,d_c) * SUM_w BinomialPMF(w;m,q_c) * 1[w>m-w AND mcnemar_exact_two_sided(w,m-w)<=familywise_alpha/4]",
    "zero_or_invalid_discordance": "BLOCK_SAMPLE_SIZE_FREEZE_AND_COLLECT_MORE_NON_CREDIT_PILOT_DATA_OR_FREEZE_AN_EXTERNAL_JUSTIFIED_RATE_WITHOUT_CONFIRMATORY_OUTCOMES"
  },
  "look_rule": {
    "primary_invocations": 1,
    "horizon": "ONE_SHOT_FIXED_HORIZON",
    "input": "ONE_CONTENT_ADDRESSED_FROZEN_SPEC_AND_RECORD_SET",
    "additional_outcome_bearing_look": "REQUIRES_A_NEW_PROSPECTIVE_PLAN_WITH_VALID_SEQUENTIAL_ERROR_CONTROL_AND_CANNOT_REWRITE_THIS_RESULT"
  },
  "provenance_gates": [
    "pinned_benchmark_root_and_protected_confirmatory_split",
    "frozen_complete_cost_model_usage_basis_and_residual_policy",
    "content_bound_common_input_arm_delta_model_and_runtime",
    "trusted_predispatch_cost_and_outcome_join",
    "final_kernel_verifier_receipt_bound_to_the_scored_artifact",
    "complete_five_arm_cells_for_every_frozen_problem",
    "independent_sampling_unit_or_a_versioned_cluster_aware_plan"
  ],
  "forbidden_claims": [
    "pilot_establishes_goal1_superiority",
    "pure_verification_gating_effect",
    "equal_realized_or_physical_compute",
    "contamination_free_generalization",
    "evaluator_pass_alone_establishes_science",
    "incomplete_cohort_supports_primary_inference"
  ]
}
```

## Primary estimand and paired decision rule

For each frozen problem and control `c`, let `Y=1` only when the final artifact
has an admissible kernel-verifier receipt. `W_c` counts problems solved only by
the verified-chain arm; `L_c` counts problems solved only by control `c`.
The reported paired risk difference is `(W_c-L_c)/N`.

Each of the four controls receives the repository's exact two-sided McNemar
test. The four p-values are corrected together by Holm step-down at the
`ExperimentSpec.familywise_alpha` value. Goal 1 passes this primary statistical
rule only when every corrected null is rejected and `W_c>L_c` for every
control. Effect counts and effect sizes are reported even when the rule does
not reject.

The sampling unit is one frozen problem. Related or variabilized siblings may
not be treated as independent repetitions: family identities must be frozen
and the primary manifest must include at most one problem per family. Otherwise
this plan is inadmissible and a new cluster-aware plan version is required.

## Incomplete runs, failures, and retries

Primary inference requires exactly one admissible terminal outcome for every
frozen problem in all five arms. A missing cell makes the experiment
`INCOMPLETE`; no four-arm subset, complete-case subset, replacement problem, or
selective rerun receives a primary p-value.

Only symmetric retries frozen before dispatch may occur, and every attempt and
failure is charged to its cell. After a terminal outcome exists, that cell may
not be rerun for the primary analysis ID. An exhausted execution is coded
unsolved only when a separately frozen execution-to-outcome rule says so and
the dispatch/cost evidence is complete; otherwise the cell remains missing.

## Pilot-to-confirmatory power update

The pilot is separate and non-credit. It never enters the confirmatory test and
cannot select confirmatory problems, prompts, arms, effect targets, or test
direction. The meaningful paired improvement `delta_c` and the 80% familywise
power target are frozen before pilot outcomes are inspected.

For each control, the update may read only pilot size and total discordance
`D_c=W_c+L_c`. It computes `d_c=D_c/N_pilot` and, for the frozen improvement,
`q_c=(d_c+delta_c)/(2*d_c)`. It then enumerates the exact unconditional power
shown in the machine-readable contract. Planning uses `alpha/4` and requires
95% marginal power per contrast; the union bound therefore targets at least
80% probability that all four contrasts meet their planning event. The final
confirmatory size is the largest of the four required sizes. Holm, not
Bonferroni, remains the actual confirmatory multiplicity procedure.

If `d_c` is zero or inconsistent with the frozen effect target, sample size is
not guessed from win direction. More non-credit pilot data or a separately
justified external discordance rate must be frozen before confirmatory outcomes
exist.

## One-shot rule and provenance gates

There is one primary invocation at one fixed horizon over one content-addressed
specification and record set. Failed or aborted execution remains visible in the
audit trail. An additional outcome-bearing look requires a new prospective
sequential-error-control plan and cannot overwrite this result.

Before confirmatory dispatch, every provenance gate listed in the embedded
contract must validate. The tests for this document verify that the plan is
present, internally aligned with the current arm and statistics APIs, and
explicit about its limits. They do not certify that those external gates have
been satisfied.
