# Goal-1 verifier image

This image separates hostile Lean elaboration from proof checking.

- `mode=elaborate` executes candidate Lean source and emits only an NDJSON export.
- `mode=check` executes trusted challenge source, compares the two exported
  environments, enforces the axiom allowlist, and checks the solution export with
  NanoDA.

Production launches each mode in a different fresh container with no network,
no host mounts, a read-only root, bounded tmpfs and resources, a non-root user,
all capabilities dropped, and no signing key or evidence database. The host signs
only after the checker container has terminated and its inspected exit state is
consistent with its canonical response.

The image is self-contained: the candidate receives no host mount, Docker volume,
socket, network, credential, signing key, or evidence database. The source pins
are in `pins.json`; its canonical digest is bound alongside the final OCI image
digest. Those digests are authority, not a mutable tag or a version string.
