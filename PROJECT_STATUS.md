# Project status — release candidate

## Current state

### Green

- Nomad binpack/spread formulas pinned to an immutable upstream commit.
- Deterministic smoke experiment reproduces byte-identical functional JSON.
- Held-out CSV contains the complete 48-cell × 10-seed × 11-policy matrix.
- All 5,280 held-out records replay exactly on deterministic fields.
- Canonical summaries and three release plots regenerate from the held-out CSV.
- README, fidelity boundary, resume evidence, and interview guide are complete.
- Minimal Python packaging, requirements, pytest configuration, and ignore rules
  exist.

### Broken

- None known. The obsolete Phase-2 baseline entry point is an intentional
  deprecation shim rather than a broken call into removed APIs.

### Unverified

- Production Nomad impact and trace realism.
- Behavior with durations, departures, resource release, queues, or bounded
  candidate evaluation.
- Portability of saved wall-clock timing measurements; timing is excluded from
  canonical release claims.

## Priorities

### High priority

- None remaining for this release.

### Medium priority after release

- Add trace-driven workloads.
- Add durations, completion events, resource release, and queueing.
- Study candidate-budget sensitivity and an admission/consolidation objective.

### Cut before deadline

- Real Nomad integration, cloud deployment, GPUs/network/storage, ML/RL
  policies, dashboard work, and large refactors.

## Verification status

- **Tests:** 127 passing.
- **Smoke experiment:** executed twice with identical JSON output.
- **Held-out experiment:** 5,280/5,280 deterministic records replayed exactly;
  timing fields intentionally excluded from equality checks.
- **Canonical evidence:** `results/canonical/manifest.json` validates source
  shape and records the held-out CSV SHA-256.
- **Documentation:** release narrative, limitations, reproduction commands,
  evidence mapping, and interview answers present.
- **Release blockers:** none.

## Repository note

The supplied project directory contains no `.git` metadata, so `git status` and
history are unavailable. The current filesystem was treated as the source of
truth; existing historical reports and artifacts were preserved. The local
`.venv/` is excluded by `.gitignore`.

