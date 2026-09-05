# Goal 1 readiness result and blocking defect

## Outcome

**Goal 1: NOT EVALUATED. Countable attempts: 0.** The approved zero-model job
executed once and failed the product-admission fixture. Do not launch a larger
pilot, manufacture a product PASS, or repeat the successful reflexivity control.

- Code: `864c1ed58bf9504c58edda74a78da5ad5d014d7b`, branch `work/PM/G1V2-readiness`.
- [Readiness run 33948987056](https://github.com/Kitahl/Supernova-V2/actions/runs/33948987056), job `101260085364`: FAILED; diagnostics uploaded.
- Artifact `9964248572`, `zero-model-readiness-33948987056`, ZIP 12,388,309 bytes.
- ZIP SHA256: `7a0e3d63b223c11d3789640211a68e89fae438c084933f4e7b7bcbcbdcb5bf55`.
- Report SHA256: `89995a65a0db019c051183530c2ffa880751a19a732f80ae320f582790e30ece`.
- Existing verifier digest: `6e4008a00beebf5795e3afdc1affcfd549310d357a6662084450a534309b10ba`.
- No new model calls, image builds, image publication, local Docker repair,
  benchmark change, scientific activation, or merge occurred in this job.

## Host-signed observations

Times are measured total verifier elapsed time, not projected model throughput.

| Request | Outcome / cause | Seconds |
|---|---|---:|
| archive-0, amc12a_2003_p1, attempt 0 | INVALID / REJECTED | 8.928 |
| archive-1, mathd_numbertheory_48, attempt 0 | VALID / ACCEPTED | 28.742 |
| archive-2, mathd_algebra_185, attempt 0 | INVALID / REJECTED | 8.411 |
| archive-3, mathd_numbertheory_48, attempt 1 | VALID / ACCEPTED | 29.802 |
| archive-4, mathd_algebra_185, attempt 1 | INVALID / REJECTED | 8.317 |
| fixture-valid, fenced Mathlib reflexivity | VALID / ACCEPTED | 26.792 |
| fixture-prose | INVALID / REJECTED | 8.115 |
| fixture-product | UNKNOWN / INTERNAL | 30.089 |

The two archived VALIDs concern the same problem and identical proof export.
They are not two distinct solved problems, new attempts, calibration, or scientific
credit. Prose rejection is real but is not a sub-five-second result.

All observed phases exited and had observed teardown; none timed out. Product
phases: parser exit 0 in 8.381 s; elaborator exit 0 in 13.261 s; checker exit 20
in 8.408 s. The final proof-using-product fixture was **not reached**.

Independent readback verified eight Ed25519 signatures, the exact report/SQLite
record join, 48 stored blob length/digest pairs, run/arm/candidate/image bindings,
and 48 committed source/config file digests. This proves diagnostic integrity,
not readiness or authority for a countable experiment. The original archived
database and the uploaded archive-input copy both hash to
`a3a41f2c2b86edf88465ece3cad651c446cf2a1821213c946c56044331060525`.
Historical signatures remain NOT_REVERIFIABLE because their old public key was
not retained; current replay signatures do not retroactively repair that.

## Proven failure: wrong verification subject for products

`confirmatory_io.build_verification_subject()` builds a first product's challenge
from the frozen import prefix, without a declaration. `VerifierSupervisor` then
appends `sorry` to that same source for the trusted checker. The actual product
challenge is exactly:

```lean
import Mathlib

  sorry
```

The signed checker diagnostic is `Challenge.lean:3:2: error: unexpected token
'sorry'; expected command`. The product parser accepted the named declaration;
candidate elaboration/export completed; independent checking failed before
statement comparison. This is not a Lake, Docker, model, timeout, or parser crash.

The adjacent final-answer path also prepends admitted product bytes to
`challenge_source`. It therefore sends candidate-authored declaration/proof code
to trusted challenge elaboration. That contradicts the hostile-source boundary.
A read-only reproduction using the actual subject builder confirms both byte
flows. No malicious Lean source was executed to establish this finding.

## What the repair must preserve

There are two different verification objectives, currently conflated:

1. **Final proof:** hostile solution input includes admitted products and the
   exact final response; trusted challenge input contains only the original frozen
   theorem. Keep independent target/type comparison, axiom checking, and NanoDA.
2. **Product:** validate the exact named submitted declaration and its predecessor
   context, not the final theorem. Bind source/harness, expected name, predecessor
   hashes, and exported theorem; independently check theorem kind, proof,
   dependencies, and permitted axioms. State and enforce how submitted intent
   corresponds to the checked export.

The frozen product protocol does not explicitly demand elaborating the product
type twice. It does demand a bound PASS for the exact submitted product. Merely
hashing source bytes and an export together does not establish that correspondence.
The pinned Comparator checks target name/universes/type and recursively anchors
target-type dependencies and configured primitives against the challenge. NanoDA
is given export data, not source text.

An independent read-only reviewer checked these call paths and the pinned
Comparator source at `71b52ec...`. Under the hostile-metaprogram/export-substitution
threat model, accepting any valid theorem with the expected name is insufficient:
a forged export of `True` must not admit a submitted declaration of `False`.

### Do not use these false fixes

- Copy a candidate-controlled type/proof into the trusted checker and call it data.
- Compare a product export with itself or check only its name/hash.
- Change every product into a proof of the final benchmark theorem.
- Supply a host-authored header only for the fixture, then claim arbitrary model
  products are qualified.
- Trust an export's type as independent evidence of the same export's intent.

A fixed host-authored product header could exercise a diagnostic split interface,
but cannot qualify general production product admission. The general repair needs
an independent product-intent binding mechanism, or an explicitly revised trust
assumption and protocol. No such weakening was silently adopted in this turn.

## Existing corpus evidence recovered, without rerunning it

[Run 33829111921](https://github.com/Kitahl/Supernova-V2/actions/runs/33829111921)
at `48cc778ffb187eed0a224cc1ba4b7f68bb066835` produced artifact `9921544556`.
ZIP SHA256: `0408e2e2aade0904517855d6ec481ff879fcb53439d267659c2d735b87f99263`.
Its reported benchmark candidate root is
`c404215b329dcaca4228a8a23eaa21b64277c85e536c441232e91355ec96d9d8`.

- 488 statement checks: 424 PASS, 64 FAIL.
- 63 failures: `unexpected token 'in'; expected ','`.
- One failure: test-split `amc12a_2020_p15`, unknown constant `Complex.abs`.
- Median 5.7075 s, maximum 7.199 s; no failure was a 60-second timeout.
- This was a `lake env lean` compatibility job, **not** release-image signed
  verification. It is failure-localization evidence, not a qualification substitute.

Use these exact failures for individually evidenced benchmark-v2 candidate
patches; do not globally mutate frozen statements or count `sorry` checks as proofs.

## Continuation boundary

The readiness ticket is EXECUTED_BLOCKED_PRODUCT_CHALLENGE. Its fixture acceptance
criteria are not met. Control PR97 remains draft and unmerged; its full CI run
33946865429 passed. No readiness PR was opened because the existing broad PR
trigger would launch an unapproved executor image rebuild.

Next engineering unit: introduce separate hostile-solution/trusted-challenge
interfaces and a reviewed product-intent binding route. Required tests include
export/type substitution, product bytes absent from trusted challenge execution,
byte-exact admitted context in the hostile solution, and immutable run/arm/source
evidence. Preserve this failed artifact. Only after real product admission AND
final use pass may the non-credit model canary be considered.

Still not established: full benchmark-v2 compatibility, product mechanism
qualification, validation calibration, prospective sample size/protocol/complete
cost seal, countable execution, and registered final PASS/FAIL analysis.
