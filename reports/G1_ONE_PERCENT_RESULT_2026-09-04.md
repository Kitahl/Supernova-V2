# Goal 1 one-percent validation result — 2026-09-04

## Decision

**STOP. Do not advance to a larger calibration or the countable Goal 1
experiment.**

The pilot executed its full non-credit schedule and passed its mechanical
integrity gate. It did not establish a usable verified-chain treatment with the
pinned model and current output contract.

- Scientific classification: **NON_CREDIT_VALIDATION_ONLY**
- Scientific credit: **NONE**
- Countable attempts before and after: **0**
- Execution integrity: **PASS**
- Capability/readiness decision: **FAIL TO QUALIFY FOR SCALING**

## Immutable evidence

- Repository: `Kitahl/Supernova-V2`
- Commit: `23018964e0bcd5cfdf803eeaf31d3f386645a3cd`
- One-shot tag: `run-goal1-pilot-1pct-v2-20260903-v1`
- GitHub Actions run: `33843676746`
- Job: `100930921102`
- Verifier:
  `ghcr.io/kitahl/supernova-goal1-verifier@sha256:6e4008a00beebf5795e3afdc1affcfd549310d357a6662084450a534309b10ba`
- Report SHA-256:
  `72a9381861198d8ac29eb3c06eb8fad135eacbbde628007f7576988442e03dd6`
- Evidence artifact: `9927602076`
- Evidence artifact archive digest:
  `sha256:2a087e76882a76240dbaa53ca28639dc5614a603aede04af10b72dd38092446f`

The check-run annotation exposes only bounded aggregate measurements, not
candidate text. The full evidence artifact is 3,222,996 bytes and exists until
2026-12-03 according to GitHub's artifact metadata.

## Frozen schedule actually executed

- Five validation problems
- Two declared arms: `ordinary` and `verified_chain`
- Two attempts per problem-arm
- Twenty total typed attempts
- Ten attempts per arm
- Test split untouched

Selected problems, in frozen order:

1. `amc12a_2003_p1`
2. `amc12b_2003_p6`
3. `mathd_numbertheory_48`
4. `amc12a_2020_p13`
5. `mathd_algebra_185`

## Observed results

| Measurement | Ordinary | Verified chain | Total |
| --- | ---: | ---: | ---: |
| Scheduled attempts | 10 | 10 | 20 |
| Answered | 6 | 6 | 12 |
| Model errors | 4 | 4 | 8 |
| No-answer outcomes | 0 | 0 | 0 |
| Malformed answered outputs | 1 | 6 | 7 |
| Signed INVALID | 6 | 6 | 12 |
| Signed UNKNOWN | 0 | 0 | 0 |
| Signed VALID | 0 | 0 | 0 |
| Solved problems | 0 | 0 | 0 |

Product-chain mechanism:

- Product emissions: **0**
- Product emission rate: **0 / 10 = 0%**
- Product admissions: **0**
- Admission rate given emission: **undefined** because nothing was emitted
- Later final attempts exposed to an admitted product: **0**

All five paired problem outcomes were concordant failures: neither arm solved
any selected problem at best-of-two. There are therefore zero discordant pairs
and no treatment-effect information in this pilot.

## Timing

- Workflow: 2026-09-04 06:16:22Z to 07:30:20Z
- Exact validation stage: 06:22:22Z to 07:30:09Z
- Validation-stage wall time: **4,067 seconds = 67 minutes 47 seconds**
- Naive stage-time average: **203.35 seconds per scheduled attempt**

That average includes the signed pre-model gates and must not be treated as a
stable per-attempt latency estimate. It is, however, enough to reject the
earlier assumption that this exact configuration would finish 20 attempts in
roughly 25–40 minutes.

## Interpretation

### What worked

1. The one-shot tag launched only the intended non-credit stage.
2. The executor image built and passed its networkless preflight.
3. The frozen benchmark-v2 validation split reconstructed successfully.
4. The previously qualified verifier was resolved to an immutable registry
   digest and requalified before dispatch.
5. Exactly 20 attempt records were produced.
6. Every answered response received signed verifier evidence.
7. Malformed outputs became typed failed attempts rather than aborting the run.
8. The evidence artifact uploaded and the workflow completed successfully.

### What did not work

1. The model-call error rate was **40% in each arm**.
2. None of the 12 answered responses was Lean-valid.
3. The verified-chain model never produced one protocol-valid product or final
   response: all six answered chain responses were malformed.
4. Because no product was emitted, the pilot never exercised product
   verification, admission, memory exposure, or downstream use.

The 1% pilot therefore falsifies the operational assumption that the current
Kimina/executor/product-prompt combination can express the registered
`verified_chain` treatment. It does **not** falsify the scientific hypothesis
that verified reusable products can improve later proof attempts, because the
treatment was never instantiated.

## Unresolved evidence boundary

The current GitHub credential can read run, check, annotation, and artifact
metadata, but it lacks the `actions` scope required to download the evidence
archive. Therefore the aggregate error counts are proven, but the exact
exception class for the eight model errors and the exact raw shape of the six
malformed chain responses are not yet independently extracted here.

Do not guess those causes. The next analysis input must be the exact
`one-percent-report.json` from artifact `9927602076`, or an equivalent
bounded export of:

- `model_error` and `model_error_detail` for all eight model errors;
- `adaptation_rule`, `classification_error`, and raw-response hashes for
  all answered chain attempts;
- phase timings for all signed verifier records.

## Next allowed action

Analyze the existing artifact; do not launch another model run first.

After exact causes are known:

1. repair the model-call error mechanism;
2. repair product-protocol compliance without silently reclassifying unmarked
   text;
3. prove one protocol-valid product emission and one signed product admission
   in a non-credit targeted qualification;
4. rerun a bounded pilot only after those two failures are closed.

No larger calibration and no countable Goal 1 experiment is authorized by this
result.
