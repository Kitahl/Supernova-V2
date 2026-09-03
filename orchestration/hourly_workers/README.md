# Goal 1 hourly worker pool

This directory coordinates fourteen existing scheduled-task identities as bounded
engineering and research workers. `BIL00` is intentionally excluded from this
pool because its registered role is status-only.

## Authority and update flow

1. `MASTER_WORK_ORDER.json` is the manager-owned assignment authority.
2. Each worker reads only its own assignment and writes detailed work to
   `worker_reports/hourly/<role>/`.
3. Each worker updates only its own `STATUS.json` shard.
4. The worker runs `python scripts/render_hourly_worker_master.py`; this
   deterministically rebuilds `MASTER_STATUS.md` from all fourteen shards.
5. Workers use one role/work-item branch and one pull request. They never merge
   `main` and never overwrite another worker's files.

This sharded design prevents fourteen hourly workers from concurrently editing
one mutable JSON object. `MASTER_STATUS.md` is a materialized view and is never
edited by hand.

## Hourly behavior

On every wake a worker must:

1. fetch current `main` and verify its exact scheduler identity in
   `orchestration/TASKS.json`;
2. read `AGENTS.md`, this file, and `MASTER_WORK_ORDER.json`;
3. continue the existing branch/PR for its assigned work item, or create it from
   current `main` if none exists;
4. perform one bounded engineering, testing, review, or primary-source research
   increment within `allowed_paths`;
5. run the assignment's verification commands where possible;
6. write a timestamped evidence report and update its own `STATUS.json` with
   what changed, exact commands/results, blockers, and `next_goal`;
7. regenerate `MASTER_STATUS.md` and open or update the same PR.

If no material progress is possible, the worker records a typed blocker with
the exact missing input or failing command. It does not manufacture progress or
repeat an unchanged report.

## Global prohibitions

- No production or countable model attempt.
- No activation nonce consumption.
- No mutation of frozen Goal-1 scientific inputs or sealed protocol artifacts.
- No secrets, credentials, private task transcripts, or private task metadata.
- No Docker pruning or other destructive cleanup.
- No task creation, replacement, identity change, or work outside the assigned
  role and paths.
- No acceptance based only on a worker's claim; CI or deterministic local
  verification is required.
