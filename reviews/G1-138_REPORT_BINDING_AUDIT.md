# G1-138 — Report-access binding audit

**Ticket:** `G1-138`  
**Role:** `MF01`  
**Audit base:** `main@6d95d2bfdfd6b7e910377ef1124d0a70a696a2c7`  
**Scope:** engineering trace and implementation handoff only. This document changes no frozen scientific input, opens no execution gate, provisions no authority, dispatches no model call, and makes no scientific result claim.

## 1. TLDR verdict

**Verdict: HANDOFF_READY_WITH_BINDING_GAPS.**

The merged report-access attestation is internally specific and is statically cross-checked against the frozen benchmark/protocol, but the live production path does not yet carry its identity through execution authority, activation, supervisor, or production-manifest authorization. The required fix is operational and additive: bind the exact attestation and trusted-host supervisor source in the later integrated authority/manifest capability without modifying `goal1/CONFIRMATORY_PROTOCOL.json`, its sealed scientific rules, benchmark membership, prompts, attempts, budgets, or arm allocation.

The critical invariant is:

> Public reconstructibility establishes where the report corpus can be reconstructed from. Integrity establishes which exact report/attestation bytes are authorized. Statement fidelity establishes that a dispatched problem/request corresponds to the exact frozen problem bytes. None of these three propositions implies either of the other two.

## 2. Exact evidence snapshot

All Git blob IDs below are from the audit base commit.

| Evidence | Exact identity | Relevant contract |
|---|---|---|
| `goal1/CONFIRMATORY_REPORT_ACCESS_ATTESTATION.json` | Git blob `dd599b8bc11a08689a3eecfc46bdbba1ffd6a095` | `status=SEALED`; `claim_scope=EXPERIMENT_TIME_REPORT_ACCESS_NOT_DATA_SECRECY` |
| frozen report payload named by the attestation | SHA-256 `5abc8ff963f096bc28107f09d96d04592971ff1f771c15dba77c5649e541970a`; 231,909 bytes; 244 records | exact `test.jsonl` content contract |
| `goal1/CONFIRMATORY_BENCHMARK.json` | Git blob `ade21f86d9566ce863ac09acd4c9103a48080ef4` | benchmark freeze `goal1-confirmatory-benchmark-v1` |
| `goal1/BENCHMARK_SOURCES.json` | Git blob `6fc789fe09bc27338a790ebd3f7fbfefeac9c38d` | pinned public reconstruction sources |
| `goal1/CONFIRMATORY_PROTOCOL.json` | Git blob `65d65e36a32aa1a73de44b1d2443c9587a14dacb` | sealed-rules SHA-256 `f1e650bc1f33d083c92f4df2a314bef79f8f646fa23431e39a2ebb83b28212e9` |
| `goal1/GOAL1.json` | Git blob `e38f722ddcd3464095423d2ed91001e961626934` | preexecution authority still has `held_out_dispatch=BLOCKED` |
| `goal1/CONFIRMATORY_EXECUTION_AUTHORITY.json` | Git blob `36dea9c9ef8fdd3b692a056b462ab5c0127e4bc5` | signed execution authority binds protocol/GOAL1/runtime but has no report-attestation field |
| `src/supernova_goal1/execution_authority.py` | Git blob `a239ae8eb4b7d3baa7ca4bbbd18db715e4353d1e` | `_AUTHORITY_FIELDS`, `ValidatedExecutionAuthority`, `_validate_authority_artifact`, `load_execution_authority` |
| `src/supernova_goal1/activation.py` | Git blob `f45895d64a59a191946dc0c360c080bbddf0d983` | `_open_operational_gate`, `activate_confirmatory_execution` |
| `src/supernova_goal1/confirmatory_manifest.py` | Git blob `2f347aa758a9f2d790ccb56fb4f4c65953ee61e3` | `_validate_protocol`, `_bind_authorized_manifest`, `_build_authorized_confirmatory_manifest`, `assert_dispatch_authorized` |
| `src/supernova_goal1/confirmatory_supervisor.py` | Git blob `23ada4856b4e4533f8b0acf0bbdaa388bb50b331` | `load_repository_execution_bindings`, `provision_repository_execution_authority`, `run_supervised_attempt` |
| `tests/test_confirmatory_benchmark.py` | Git blob `be1dcddcac168b16ab1687149a6b35e6f314d2fc` | static attestation/benchmark/protocol semantics |
| `tests/test_confirmatory_execution_authority.py` | Git blob `c385a59b09955aaa76e7f89fbed85c8d14db0816` | current authority + activation behavior |
| `tests/test_confirmatory_manifest.py` | Git blob `19d93156c2d652fd4c48bd859dfb4e8fbb05ebc2` | current manifest binding set and draft/production gate |

## 3. What the merged attestation proves

`goal1/CONFIRMATORY_REPORT_ACCESS_ATTESTATION.json` binds all of the following without asserting secrecy:

1. Exact report payload: `test.jsonl`, 244 records, 231,909 bytes, SHA-256 `5abc8ff963f096bc28107f09d96d04592971ff1f771c15dba77c5649e541970a`.
2. Frozen benchmark blob: `ade21f86d9566ce863ac09acd4c9103a48080ef4`.
3. Frozen protocol blob: `65d65e36a32aa1a73de44b1d2443c9587a14dacb`.
4. Frozen sealed-rules SHA-256: `f1e650bc1f33d083c92f4df2a314bef79f8f646fa23431e39a2ebb83b28212e9`.
5. Public source manifest blob: `6fc789fe09bc27338a790ebd3f7fbfefeac9c38d`.
6. Pinned upstream DeepSeek and AI-MO source identities.
7. A controlled-runtime rule: public availability does not authorize experiment-time report use; confirmatory injection remains blocked until protocol rules, execution authority, and manifest are all sealed.

`tests/test_confirmatory_benchmark.py::test_report_access_attestation_binds_public_report_and_runtime_gate` checks the report payload, benchmark binding, protocol rules binding, public reconstructibility claim, and runtime gate. `test_frozen_release_terms_mean_runtime_injection_not_data_secrecy` separately checks the interpretation of the legacy word “release”.

This is a static repository attestation. It is not currently a production dispatch capability.

## 4. Live production trace and missing bindings

### 4.1 Execution-authority boundary — MISSING

`execution_authority.py::_AUTHORITY_FIELDS` is closed-world and currently contains no report-access-attestation or supervisor-source identity. `ValidatedExecutionAuthority` likewise carries authority, model, executor, receipt, and root identifiers but no report-attestation digest.

`load_execution_authority(protocol, goal1)` reloads the fixed trust root, fixed protocol, fixed GOAL1, the execution-authority artifact, and launcher/capacity bindings before calling `_validate_authority_artifact`. It does not load or validate `goal1/CONFIRMATORY_REPORT_ACCESS_ATTESTATION.json`.

Deterministic consequence at the audit base: execution-authority validation is insensitive to deletion or substitution of the report-access attestation, provided all artifacts it currently reads remain unchanged. Therefore execution authority alone cannot prove that the report-injection contract being used is the merged G1-134 contract.

**Handoff constraint:** do not add a field ad hoc to the existing signed `CONFIRMATORY_EXECUTION_AUTHORITY.json`. `_AUTHORITY_FIELDS` is exact, and changing the signed body changes derived authority bytes and requires deliberate reprovisioning. G1-143 owns dry-run reprovisioning; G1-136 concurrently changes the external trust-root boundary. The report binding should enter through the reviewed integrated authority capability, then derived public authority artifacts can be regenerated once by the designated reprovisioning path.

### 4.2 Activation boundary — MISSING

`activation.py::activate_confirmatory_execution(protocol, goal1, *, operator_seed)` currently:

1. calls `_open_operational_gate(protocol)`;
2. calls `confirmatory_manifest._build_authorized_confirmatory_manifest(...)`;
3. calls `assert_dispatch_authorized(...)`;
4. returns `ConfirmatoryActivation`.

Neither `_open_operational_gate` nor `activate_confirmatory_execution` loads, hashes, or validates the report-access attestation. The existing fixed-repository activation test in `tests/test_confirmatory_execution_authority.py::test_fixed_repository_authority_activates` reaches `PRODUCTION_CREDIT_STATUS` using the current authority path without an attestation argument or attestation-bound capability.

**Required integrated invariant:** an activation must not become dispatch-authorized unless the capability used to build/validate the production manifest includes the exact reviewed report-access-attestation binding. An invalid or missing report binding is an invalid activation and must not consume the future one-shot production nonce.

### 4.3 Manifest boundary — MISSING and cannot be fixed by mutating sealed rules

`confirmatory_manifest.py::_validate_protocol` requires the frozen `sealed_rules.confirmatory_manifest_interface.binds` set to be exactly:

- `protocol_rules_sha256`
- `execution_authority_sha256`
- `benchmark_selection_sha256`
- `family_map_sha256`
- `cost_policy_sha256`
- `runtime_sha256`
- `model_identity_sha256`
- `schedule_sha256`
- `all_19520_dispatch_records_sha256`

There is no report-attestation binding. `_bind_authorized_manifest` currently changes only the execution-authority/model-identity bindings before marking the manifest production-credit eligible.

The frozen protocol explicitly says the manifest must be sealed before report-byte release or first confirmatory dispatch. However, adding `report_access_attestation_sha256` to the frozen protocol `binds` array would change the sealed scientific-rules digest and violates the tranche requirement to preserve frozen scientific inputs.

**Required implementation shape:** G1-144 should add an operational production binding outside the frozen scientific-rule object, or consume an integrated authority capability whose identity commits the report attestation. Draft-manifest reconstruction must remain deterministic and non-credit under the existing sealed-rules digest. Production reconstruction must reject missing or mismatched operational report-attestation bindings before setting `PRODUCTION_CREDIT_STATUS` or `AUTHORIZED_DISPATCH_STATUS`.

### 4.4 Trusted-host supervisor boundary — MISSING

`confirmatory_supervisor.py::run_supervised_attempt` binds the request and response byte digests into its host receipt. This is useful request/response integrity, but it does not establish that the request was built from the exact attested report corpus.

The supervisor currently has no report-attestation loader and no exact supervisor-source binding. Therefore a host receipt can prove “these request bytes were run under this validated executor” without proving “these bytes were authorized by the exact report-access contract”.

**Required implementation shape:** G1-141 should expose a narrow validated host-side binding containing at least:

- exact reviewed supervisor source identity;
- exact report-access-attestation byte identity;
- attested exact report payload SHA-256 `5abc8ff963f096bc28107f09d96d04592971ff1f771c15dba77c5649e541970a`;
- benchmark/protocol identities already named by the attestation.

The host must fail closed on absent/malformed/substituted attestation or source identity. This binding is operational authority evidence; it must not rewrite the frozen protocol.

## 5. Three evidence classes that must remain separate

| Evidence class | What it proves | Current evidence | What it does **not** prove |
|---|---|---|---|
| Public reconstructibility | the corpus can be reconstructed from pinned public upstream sources | `CONFIRMATORY_REPORT_ACCESS_ATTESTATION.json.public_reconstructibility` + `BENCHMARK_SOURCES.json` | runtime authorization, exact runtime bytes, request statement fidelity |
| Integrity / authorization binding | the runtime is using the exact authorized attestation/report identity | report SHA-256 exists statically, but live activation/authority/manifest plumbing is missing | that each prompt/request contains the exact intended theorem statement |
| Statement fidelity | each dispatched `problem_id` / `problem_sha256` / request statement corresponds to the frozen report record | lower-level requests already carry `problem_sha256`, `request_artifact`, and manifest/dispatch identities | public reconstructibility or authority to expose/use report bytes |

Do not use the public-source provenance claim as a substitute for an integrity check. Do not use `request_artifact_sha256` as a substitute for proving that the request came from the frozen report record. Do not interpret either as proof of model-training decontamination; the merged benchmark contract explicitly disclaims that claim.

## 6. Concrete implementation handoff

### H1 — MF05 / G1-141: supervisor bindings

Target paths already dispatched by the board:
- `src/supernova_goal1/confirmatory_supervisor.py`
- `tests/test_confirmatory_supervisor_bindings.py`

Required tests:

1. exact current attestation + exact source identity validates;
2. missing attestation fails closed;
3. one-byte attestation substitution fails;
4. benchmark/protocol/report digest substitution inside an otherwise well-formed attestation fails;
5. one-byte supervisor-source substitution fails;
6. public reconstructibility fields alone cannot authorize `run_supervised_attempt`;
7. no private key, credential, model call, or production signature is created by the binding validator itself.

### H2 — MM04 / G1-144: production-manifest operational bindings

Target paths already dispatched by the board:
- `src/supernova_goal1/confirmatory_manifest.py`
- `tests/test_confirmatory_manifest_bindings.py`

Required tests:

1. draft manifest remains deterministic and `NON_CREDIT_DRAFT`;
2. current frozen protocol blob and sealed-rules SHA remain unchanged;
3. production reconstruction rejects absent `report_access_attestation` binding;
4. production reconstruction rejects a one-bit/one-byte attestation-digest substitution;
5. production reconstruction rejects supervisor-source binding substitution;
6. operational binding changes affect the production manifest/capability identity but do not change problem membership, families, arm allocation, prompts, attempt quota, budget, or deterministic schedule;
7. `PRODUCTION_CREDIT_STATUS` and `AUTHORIZED_DISPATCH_STATUS` are unreachable until all operational bindings validate.

### H3 — MF03 / G1-139 plus later integration: activation ordering

G1-139 owns durable one-shot activation. The integration test must establish this ordering:

`validate external trust -> validate execution authority -> validate supervisor/report binding -> validate production manifest -> consume activation nonce exactly once -> permit report injection/dispatch`

Negative tests:

1. absent/mismatched report binding does not consume the nonce;
2. manifest mismatch does not consume the nonce;
3. valid first activation consumes the nonce;
4. replay/reuse/second activation is rejected;
5. no report bytes are injected before the manifest and activation state are durably sealed.

If G1-139 cannot accept the new binding because its parallel API lands first, the later integration ticket must perform this threading; do not widen G1-139 beyond its declared paths by editing another owner’s component.

### H4 — G1-136/G1-143 integration constraint

G1-136 is changing the external runtime trust-root boundary; G1-143 owns deterministic authority reprovisioning. The final integrated authority must not treat repository content as the production root of trust. Repository attestation/source bytes are evidence to be authenticated under the external host trust boundary, not a replacement root.

Any signed/derived authority artifact that changes because of the new operational binding must be regenerated through the explicit reprovisioning path after reviewed source heads are fixed. G1-138 itself must not reprovision or sign anything.

## 7. Deterministic regression matrix

| ID | Perturbation | Expected result |
|---|---|---|
| RB-01 | delete attestation | BLOCKED before production manifest/activation |
| RB-02 | one-byte attestation mutation | BLOCKED digest mismatch |
| RB-03 | substitute report SHA-256 inside attestation | BLOCKED |
| RB-04 | substitute benchmark Git blob binding | BLOCKED |
| RB-05 | substitute protocol Git blob or sealed-rules SHA | BLOCKED |
| RB-06 | substitute supervisor source identity | BLOCKED |
| RB-07 | provide correct public-source provenance but wrong report bytes | BLOCKED |
| RB-08 | provide correct report SHA but wrong per-problem statement/request | BLOCKED by statement-fidelity/request binding, not “reconstructibility” |
| RB-09 | valid binding but stale/invalid external trust root | BLOCKED |
| RB-10 | valid binding + invalid/consumed activation nonce | BLOCKED |
| RB-11 | exact integrated binding and first valid nonce | eligible to proceed to dispatch gate; **not** a scientific PASS |
| RB-12 | mutate frozen protocol to add new binding | REJECT; scientific input mutation |

## 8. Frozen-state preservation checks

The implementation handoff must preserve all of the following exact scientific inputs:

- `goal1/CONFIRMATORY_BENCHMARK.json` Git blob `ade21f86d9566ce863ac09acd4c9103a48080ef4`;
- `goal1/CONFIRMATORY_PROTOCOL.json` Git blob `65d65e36a32aa1a73de44b1d2443c9587a14dacb`;
- sealed-rules SHA-256 `f1e650bc1f33d083c92f4df2a314bef79f8f646fa23431e39a2ebb83b28212e9`;
- report payload SHA-256 `5abc8ff963f096bc28107f09d96d04592971ff1f771c15dba77c5649e541970a`;
- 244 report problems, all five arms, 16 attempts per problem/arm, and 19,520 registered dispatch records.

Operational authority hardening may add bindings around these values. It must not redefine them.

## 9. Audit conclusion

**Current state:** `BLOCKED_FOR_INTEGRATED_BINDING`, not scientific FAIL.

The report-access attestation is a valid static provenance/runtime-use contract at the repository layer, but it is not yet carried through the live execution-authority/activation/manifest/supervisor chain. The parallel tickets G1-141 and G1-144 provide the correct code seams; G1-139 and the later integration pass must enforce ordering with one-shot activation; G1-136/G1-143 must preserve external trust-root and deterministic reprovisioning semantics.

No production execution, authority reprovisioning, signing, report injection, frozen-input mutation, or scientific adjudication is authorized by this audit.
