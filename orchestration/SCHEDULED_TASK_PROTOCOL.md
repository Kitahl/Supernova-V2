# Hourly scheduled-task protocol

The fifteen existing ChatGPT scheduled tasks are workers, not authorities.

On every wake, a task:

1. reads `orchestration/TASKS.json` and confirms its exact role and scheduler ID;
2. reads its ticket from `orchestration/BOARD.json` on `main`;
3. if the ticket is `DONE` or `WAITING`, reports the state and makes no
   GitHub change;
4. if a `READY` ticket already has a PR, reviews CI and advances that same PR;
5. otherwise creates `work/<role>/<ticket-id>` from current `main`, changes
   only declared paths, runs the acceptance tests, and opens a PR;
6. records exact commands and observed output in the PR body.

Ticket states:

- `READY`: every declared dependency is `DONE`, so the assigned task may
  implement or advance the ticket;
- `WAITING`: at least one dependency or an explicit project-manager transition
  is pending;
- `DONE`: the ticket is merged and its `completion` object binds the pull
  request number and exact merge commit.

Dependencies:

- every ticket carries a `depends_on` list of current ticket IDs;
- dependencies must exist, must not contain the ticket itself, and must form an
  acyclic graph;
- a non-supervisor ticket with an unresolved dependency remains `WAITING`;
- a non-supervisor ticket with no unresolved dependency is `READY` unless it is
  already `DONE`;
- only the project manager changes dependency-derived ticket state, in a reviewed
  orchestration pull request;
- dependency completion never changes a scheduler task identity.

Rules:

- no task pushes or merges `main`;
- no task edits another role's ticket or branch;
- no result is accepted because a task says it works;
- missing hourly output leaves a ticket pending and blocks nothing else;
- tasks never store credentials or private task metadata in the repository;
- retries are idempotent: continue the existing ticket PR rather than creating a
  duplicate;
- a `DONE` ticket is never reopened in place;
- `BOARD.json` contains exactly one current ticket per exact scheduled-task role;
- before reassigning a completed role, the project manager moves the terminal ticket
  and its exact PR/merge evidence to `ARCHIVE.json`, then assigns a new ticket ID in
  `BOARD.json` and updates only that role's `TASKS.json.ticket_id`;
- reassignment never changes, creates, or replaces a `scheduler_task_id`;
- a tranche transition updates `BOARD.json`, `TASKS.json`, `ARCHIVE.json`,
  this protocol when semantics change, and its orchestration tests in one pull
  request.

MM06 is review-only, MF06 is integration-test-only, and BIL00 is status-only.
Those supervisor tickets remain `WAITING` until the project manager explicitly
dispatches their already-satisfied dependency set. The project manager merges
after independent verification.
