# Goal-1 benchmark/runtime checker

`scripts/verify_benchmark_runtime.py` is a deterministic, no-model preflight for
the frozen Goal-1 benchmark and Lean runtime. It answers one narrow question:

> Do a root-seeded, bounded sample of **validation** statements still elaborate
> under the exact committed Lean 4.33.1 + Mathlib runtime?

It does **not** solve benchmark problems, measure model quality, inspect the test
split, freeze the complete-cost model, or create scientific Goal-1 credit.

## Inputs and identity

The checker consumes:

- a local assembled benchmark directory containing the exact files committed by
  `goal1/BENCHMARK.lock.json`;
- the frozen lock itself;
- `runtime/lean/lean-toolchain`;
- `runtime/lean/lakefile.toml`;
- `runtime/lean/lake-manifest.json`.

It verifies the whole benchmark directory against the lock before invoking Lean.
That hashes both frozen files, but only `validation.jsonl` is parsed. The test
records remain outside the development check.

`runtime_sha256` is SHA-256 over canonical JSON containing the exact bytes,
lengths, and paths of the three runtime identity files plus the exact toolchain
string. Consumers of `FrozenProblemRequest.runtime_sha256` should use this value.
The checker also rejects a toolchain other than
`leanprover/lean4:v4.33.1`, a Mathlib requirement other than `v4.33.1`, or any
manifest dependency not pinned to a full commit.

## Bounded sample

The default sample is 3 records and the hard maximum is 8. Selection is the
lowest hashes under:

```text
SHA256(benchmark_root_sha256 || NUL || "validation" || NUL || problem_id)
```

This makes the selection deterministic and tied to the frozen benchmark root,
rather than allowing an operator to cherry-pick easy statements.

Each selected benchmark record must end exactly in the assembled unsolved form
`:= by\n`. The checker appends `sorry` in a temporary file and asks the pinned
Lean kernel to elaborate it. This checks imports, syntax, names, and theorem
statement compatibility. Because a placeholder is used, every output is labelled
`NON_CREDIT_STATEMENT_ELABORATION_ONLY`; a PASS is never proof of a solution.

Temporary Lean files are created outside the repository and removed after the
run. The script performs no model or API calls and never runs `lake update`.

## Usage

Bootstrap the already-pinned runtime as described in `runtime/lean/README.md`.
Then run:

```sh
python scripts/verify_benchmark_runtime.py /absolute/path/to/assembled-benchmark
```

Optional bounded size:

```sh
python scripts/verify_benchmark_runtime.py /absolute/path/to/assembled-benchmark --sample-size 5
```

The command emits exactly one compact JSON object. Exit code `0` means the
non-credit check passed. Exit code `2` means a structural checker error, wrong
runtime, or one or more failed/timeout/error sample elaborations.

Important report fields:

- `status`: `PASS`, `FAIL`, or `ERROR`;
- `credit`: always `NON_CREDIT_STATEMENT_ELABORATION_ONLY`;
- `benchmark.root_sha256` and `benchmark.lock_sha256`;
- `runtime.runtime_sha256` and the observed Lean version line;
- `sample.problem_ids` and the deterministic selection rule;
- per-problem output digests and structured `failures`.

## Operational boundaries

- Do not point the checker at a symlink or junction benchmark/runtime root.
- Do not put the lock inside the benchmark directory.
- Do not change the toolchain, Lake file, dependency manifest, benchmark files,
  sample rule, or size after a scientific run is registered.
- A missing benchmark checkout or missing Lean executable is a machine-readable
  preflight failure, not evidence about Goal 1.
- The real experiment remains blocked until the benchmark and complete-cost
  model are both frozen and all five execution arms are integrated.
