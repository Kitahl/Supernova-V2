# Supernova V2

Supernova V2 is a falsifiable experiment about verified mathematical composition.

## Goal 1

Test whether a solver that builds a within-problem chain of independently verified
intermediate products solves more problems than four controls under the same frozen
complete-cost budget:

1. ordinary solving;
2. portfolio solving;
3. product-only solving;
4. multi-fidelity solving;
5. verified-chain solving (the candidate mechanism).

The repository starts deliberately small. GitHub is the shared mailbox and CI runner;
it is not scientific authority. The exact experiment contract is
[`goal1/GOAL1.json`](goal1/GOAL1.json), the hourly work board is
[`orchestration/BOARD.json`](orchestration/BOARD.json), and the deterministic
evaluator lives in `src/supernova_goal1`.

## Local check

```bash
python -m unittest discover -s tests -p "test_*.py"
python scripts/evaluate_goal1.py goal1/GOAL1.json examples/dry_run_records.json
```

The example is a dry run of the data path, not evidence for the hypothesis.
