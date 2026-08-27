# Contribution contract

- Work on one ticket from `orchestration/BOARD.json` at a time.
- Use branch `work/<role>/<ticket-id>` and open a pull request; never push `main`.
- Change only the ticket's declared paths unless the acceptance criteria require a
  narrowly documented adjacent change.
- Run the ticket's tests and include the exact command and output in the PR body.
- Treat model-written claims as untrusted until CI or a deterministic verifier checks
  them.
- Do not add secrets, credentials, task transcripts, or private task metadata.
- Do not change `goal1/GOAL1.json` while an experiment is running.
