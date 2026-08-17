# Project status — Phase 4 release candidate

## Green

- Phase 1–3 static admission behavior and all 127 original tests are preserved.
- Phase 4 adds deterministic arrivals, durations, completions, exact resource
  release, waiting/backfill, horizon snapshots, optional drain, and a complete
  lifecycle ledger.
- Event ordering and queue behavior are explicit and covered by hand-worked,
  invariant, replay, isolation, and metric tests.
- The complete audited suite has 171 passing tests (127 original + 43 Phase 4
  tests + 1 final statistics regression test).
- The temporal smoke artifact is byte-identical across repeated runs.
- Tuning-seed calibration froze `rho=0.70/1.00/1.30` before held-out execution.
- The held-out temporal CSV contains the complete 8 × 2 × 3 × 10 × 4 matrix:
  1,920/1,920 paired primary policy runs.
- Canonical temporal tables and four plots regenerate only from validated raw
  held-out results.
- README, temporal design, experiment report, resume evidence, limitations,
  and interview material distinguish static admission from temporal work.

## Measured outcome

- Low load: identical policy throughput and essentially zero horizon backlog.
- Near saturation: Tetris is slightly lower-throughput overall than binpack
  (`-0.036` jobs/time unit, 95% seed-blocked CI `[-0.053, -0.019]`).
- Overload: Tetris is higher-throughput overall (`+0.256`, 95% CI
  `[+0.201, +0.312]`) with 9.41 fewer queued jobs at the horizon, but uses 1.19
  more time-weighted active nodes and has 0.53 higher drained P95 wait.
- High-load hybrid: smaller throughput gain (`+0.162`) and smaller active-node
  cost (`+0.86`) than Tetris.
- EWMA/future-fit remains a Phase 3 admission-only negative result and was not
  evaluated temporally.

## Known limitations

- Synthetic arrivals/durations and a finite horizon; no production traces or
  proof of asymptotic queue stability.
- CPU/RAM and one FIFO-scan/backfill queue discipline only.
- No network, disk, devices, constraints, priority, failures, preemption,
  migration, autoscaling, or real workload execution.
- Full-scan greedy placement rather than full Nomad behavior or a global
  optimum.

## Verification status

- Tests: 171 passing.
- Phase 3 smoke: repeated with byte-identical functional JSON.
- Phase 4 smoke: repeated with byte-identical JSON and allocation invariants.
- Held-out temporal study: 1,920 records validated and retained.
- Canonical evidence: manifest records the raw CSV SHA-256 and matrix shape.
- Independent statistics audit: aggregate intervals use ten seed-level blocks
  across the predeclared workload/cluster strata; family comparisons are
  explicitly exploratory and unadjusted for multiplicity.
- Release blockers: none known after the local audit correction.

The Phase 4 release commits were already synchronized with `origin/main` when
the final audit began. The statistics-audit correction is local pending review
and an explicit push.
