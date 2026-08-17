# Resume evidence

Only the facts below are approved for resume use. All numerical claims point to
an executable check or generated artifact.

## Verified engineering facts

### Test suite

- **127 tests pass.**
- Evidence: `python -m pytest` release run and the `tests/` suite.
- Coverage focus: Nomad formula boundaries, base-10 equivalence, feasibility
  filtering, immutable state, deterministic workload generation, tie behavior,
  metrics, experimental policies, seed-split discipline, and paired statistics.

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

- Built a deterministic Python simulator reproducing HashiCorp Nomad's CPU/RAM
  binpack and spread fit scoring, with immutable cluster state, seeded workload
  generation, and 127 tests covering scoring, feasibility, determinism, and
  experiment invariants.
- Evaluated 11 placement policies and ablations across 48 workload/cluster/load
  configurations and 10 held-out seeds, finding that Tetris-style resource
  alignment improved admission by 1.6–3.7 percentage points on four mixed-shape
  workloads.
- Quantified the admission–consolidation trade-off (29.4 active nodes for
  Tetris vs. 23.0 for binpack on 30-node clusters) and found that EWMA-based
  demand prediction added no material gain over the simpler stateless heuristic.

## Do not claim

- production Nomad was modified or improved;
- throughput, jobs/second, queueing latency, or completion time was measured;
- Tetris is always better;
- full-scan greedy placement is an oracle or global optimum;
- the synthetic results establish production impact.
