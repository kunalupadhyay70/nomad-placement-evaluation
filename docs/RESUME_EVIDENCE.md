# Resume evidence

Only the facts below are approved for resume use. All numerical claims point to
an executable check or generated artifact.

## Verified engineering facts

### Test suite

- **171 tests pass: 127 preserved Phase 1–3 tests, 43 Phase 4 tests, and one
  final seed-block statistics regression test.**
- Evidence: `python -m pytest` release run and the `tests/` suite.
- Coverage focus: Nomad formula boundaries, base-10 equivalence, feasibility
  filtering, immutable state, deterministic workload generation, tie behavior,
  metrics, experimental policies, seed-split discipline, paired statistics,
  lifecycle event ordering, queue/backfill behavior, exact release, horizon and
  drain semantics, time-weighted metrics, and identical policy traces.

### Temporal experiment design

- **48 held-out temporal cells:** 8 workload families × 2 cluster types × 3
  resource-time offered-load regions.
- **10 held-out seeds per cell and four frozen primary policies.**
- **1,920 held-out temporal policy runs:** 48 × 10 × 4.
- Deterministic Poisson arrivals, independent `Uniform[8, 12]` durations, a
  50-unit observation horizon, and exact completion-time resource release.
- Evidence: `results/temporal/config.json`,
  `results/temporal/canonical/manifest.json`, and
  `results/temporal/raw/final.csv`.

### Temporal result

At overload (`rho=1.30`), Tetris versus Nomad-style binpack across 160 paired
held-out traces:

- completed **+0.256 jobs per simulated time unit** (95% CI
  `[+0.201, +0.312]`), a **1.38%** gain over binpack's 18.563;
- left **9.41 fewer jobs queued** at the horizon;
- used **1.19 more time-weighted active nodes**;
- increased drained-job P95 waiting by **0.53 simulated time units**.

The qualification is essential: low-load throughput was identical, and near
saturation Tetris was `-0.036` jobs/time unit (95% CI
`[-0.053, -0.019]`). The clear high-load family gains were RAM-heavy, bimodal,
tiny/large, drift, and adversarial. Evidence:
`results/temporal/canonical/paired_overall_load_vs_binpack.csv` and
`paired_family_load_vs_binpack.csv`.

The high-load hybrid provided a limited compromise versus binpack: +0.162
jobs/time unit (95% CI `[+0.127, +0.196]`) for +0.86 time-weighted active
nodes, versus Tetris's +0.256 throughput and +1.19 nodes. It still increased
P95 wait.

### Experiment design

- **48 held-out experiment cells:** 8 workload families × 2 cluster
  configurations × 3 load levels.
- **10 held-out seeds per cell:** seeds 1000–1009.
- **11 policies and ablations in the held-out matrix.**
- **5,280 held-out policy runs:** 48 × 10 × 11.
- Evidence: `sim/families.py`, `scripts/run_matrix.py`,
  `results/canonical/manifest.json`, and `results/matrix/final.csv`.

The four primary saved study artifacts contain **11,040 raw policy-run rows**:

| Split/artifact | Runs |
|---|---:|
| tuning (`tuning.csv`) | 4,320 |
| validation (`val.csv`) | 440 |
| held-out (`final.csv`) | 5,280 |
| sensitivity (`sensitivity_0/1/2.csv`) | 1,000 |
| **Total** | **11,040** |

Do not add the per-family or per-cell shard files to this total; they duplicate
the merged tuning and held-out CSVs. Sensitivity is a separate stage and
intentionally reruns its own baseline records, so 11,040 is an artifact row
count—not a claim that every policy/trace key is globally unique across stages.

### Admission result

Across the four mixed-shape families, pure Tetris placement improved mean
held-out admission over Nomad-style binpack by **1.6–3.7 percentage points**:

| Family | Mean paired delta | 95% CI | Wins / losses / ties |
|---|---:|---:|---:|
| bimodal | +1.62 pp | [+0.91, +2.32] pp | 20 / 0 / 40 |
| tiny/large | +1.93 pp | [+1.17, +2.68] pp | 20 / 0 / 40 |
| drift | +3.67 pp | [+2.31, +5.03] pp | 20 / 0 / 40 |
| adversarial order | +1.93 pp | [+1.20, +2.65] pp | 20 / 0 / 40 |

Each family statistic pools 2 clusters × 3 loads × 10 paired test seeds = 60
paired observations. Forty ties per family occur in lower-load cells where both
policies usually admit every request. Evidence:
`results/canonical/paired_comparison.csv`.

### Negative and null results

- Uniformly CPU-heavy workload: Tetris was **0.23 percentage points lower** on
  average; 95% CI [−0.50, +0.04] pp, so the interval includes zero.
- The EWMA workload-mismatch policy stayed within roughly ±0.05 percentage
  points of binpack in every workload family.
- The future-fit variants did not materially or consistently outperform the
  stateless Tetris policy on held-out data.
- Evidence: `results/canonical/paired_comparison.csv` and
  `EXPERIMENT_REPORT.md`.

### Admission–consolidation trade-off

- Nomad-style binpack used **23.0 active nodes on average**.
- Tetris used **29.4 active nodes on average**.
- Each mean covers 480 held-out traces for that policy (48 cells × 10 seeds) on
  30-node clusters.
- Evidence: `results/canonical/summary.csv` and
  `results/canonical/plots/admission_vs_consolidation.png`.

This supports a trade-off claim, not a universal-winner claim: alignment
improved admission in several mixed workloads but consolidated less strongly.

### Nomad connection

- The CPU/RAM binpack and spread formulas are pinned to HashiCorp Nomad commit
  `f3fe893c53d20681232700eb67f89f7478c2fa4e`.
- Evidence: `docs/NOMAD_FIDELITY.md`, `sim/policies.py`, and
  `tests/test_policies.py`.
- Do **not** claim that this project modifies, embeds, or benchmarks production
  Nomad.

## Approved resume bullets

- Built a deterministic event-driven Python scheduler simulator reproducing a
  pinned HashiCorp Nomad CPU/RAM fit score, with Poisson arrivals, job
  completion/resource release, FIFO-scan backfilling, horizon/drain metrics,
  and validated it with 171 tests covering scoring, lifecycle invariants, and
  seed-block statistics.
- Executed 1,920 paired held-out temporal runs across 48 workload/cluster/load
  cells, finding Tetris increased overload throughput by 1.38% (paired
  absolute-delta 95% CI +0.201 to +0.312 jobs/time unit) while using 1.19 more
  time-weighted active nodes and increasing P95 wait by 0.53 simulated time
  units.
- Quantified a load-dependent result: resource-shape alignment added no
  low-load throughput, slightly reduced near-saturation throughput overall,
  and improved overload throughput most on mixed, drifting, and adversarial
  request shapes.

## Do not claim

- production Nomad was modified or improved;
- simulated throughput or waiting time is production throughput/latency;
- Tetris is always better;
- full-scan greedy placement is an oracle or global optimum;
- the synthetic results establish production impact.

The Phase 3 admission and EWMA bullets remain supported historical alternatives,
but do not combine their admission numbers with Phase 4 throughput wording.
