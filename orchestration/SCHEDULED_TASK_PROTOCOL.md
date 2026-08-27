# Hourly scheduled-task protocol

The fifteen existing ChatGPT scheduled tasks are workers, not authorities.

On every wake, a task:

1. reads `orchestration/TASKS.json` and confirms its exact role and scheduler ID;
2. reads its ticket from `orchestration/BOARD.json` on `main`;
3. if the ticket is not `READY`, reports the state and makes no GitHub change;
4. if a PR already exists for the ticket, reviews CI and advances that same PR;
5. otherwise creates `work/<role>/<ticket-id>` from current `main`, changes only
   declared paths, runs the acceptance tests, and opens a PR;
6. records exact commands and observed output in the PR body.

Rules:

- no task pushes or merges `main`;
- no task edits another role's ticket or branch;
- no result is accepted because a task says it works;
- missing hourly output leaves a ticket pending and blocks nothing else;
- tasks never store credentials or private task metadata in the repository;
- retries are idempotent: continue the existing ticket PR rather than creating a
  duplicate.

MM06 is review-only, MF06 is integration-test-only, and BIL00 is status-only.
The project manager merges after independent verification.
