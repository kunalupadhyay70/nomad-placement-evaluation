# Final independent Phase 4 statistics audit

Audit date: 2026-08-18

Audited release HEAD: `6bfb82082445a064dcdddea2389ced586ae2f664`

Pre-Phase-4 parent: `4dcee3f18aa66efd8f0e333268daa016bb555616`

Phase 4 commits: `b188ba213fdadebf579b79c3bc074d9bcd684c21`
and `6bfb82082445a064dcdddea2389ced586ae2f664`

## Verdict

**VERIFIED WITH CORRECTIONS — READY TO PUSH**

The simulator, metric ledger, raw matrix, point estimates, experiment counts,
and qualitative conclusions verified. One statistical-method issue required a
release correction: aggregate confidence intervals had treated 20 or 160
repeated seed/stratum rows as independent observations. The corrected analysis
uses ten seed-level macro-average blocks (`df=9`). The point estimates and
which high-load family intervals cross zero are unchanged. The corrected
intervals, reports, plots, and resume/interview text supersede the original
interval displays.

The audit began with a clean `main` at the audited release HEAD. Contrary to
the incoming handoff, that HEAD already equalled the locally recorded
`origin/main`; the two Phase 4 commits had been pushed before this audit. The
audit did not fetch, push, publish, rewrite history, or delete repository data.

Files corrected or added by the audit are `scripts/analyze_temporal.py`,
`scripts/verify_temporal_stats.py`, `requirements-lock.txt`, `README.md`,
`PROJECT_STATUS.md`, `TEMPORAL_EXPERIMENT_REPORT.md`,
`docs/INTERVIEW_GUIDE.md`, `docs/RESUME_EVIDENCE.md`, this audit report, and
all 11 files listed in the corrected canonical-artifact table below. The
authoritative raw calibration and held-out CSVs were not changed.

## Independent method

`scripts/verify_temporal_stats.py` is independent of
`scripts/analyze_temporal.py` and the simulator package. It reads the raw CSVs,
checks their hash and exact matrices, reconciles metric arithmetic, verifies
paired trace metadata, and recomputes the release statistics from unrounded
values. It exits nonzero on a failed invariant or headline tolerance.

The approved estimand is:

1. pair each candidate policy with binpack on exactly
   `(family, cluster, load, seed)`;
2. calculate `candidate - binpack` for each trace;
3. within each load and seed, take an equal-weight macro-average over the 16
   predeclared workload-family × cluster strata;
4. calculate the mean and two-sided Student-t 95% interval over the ten seed
   blocks with `df=9`;
5. for family/load results, average the two cluster deltas within each seed and
   again use the ten seed blocks.

The relative-throughput point estimate is
`100 * (mean_tetris - mean_binpack) / mean_binpack`. At high load this is
`1.3811168797936852%`. The distinct mean of per-pair percentages is
`1.480921462%` and is not used as the release headline.

| Load | Mean binpack throughput | Mean Tetris throughput | Absolute delta | Ratio-of-means delta |
|---|---:|---:|---:|---:|
| low | 12.607125 | 12.607125 | 0.000000 | 0.000000% |
| medium | 17.041500 | 17.005625 | -0.035875 | -0.210516% |
| high | 18.562875 | 18.819250 | +0.256375 | +1.381117% |

P95 wait is the equal-weight macro-average of each run's nearest-rank P95 over
the drained feasible-job ledger. Jobs are not pooled across runs. Family-level
intervals are exploratory and unadjusted for multiple comparisons; they are
not simultaneous 95% guarantees.

## Raw-data and lifecycle verification

| Check | Independent result |
|---|---|
| Calibration matrix | 480/480 rows; 8 families × 2 clusters × 3 loads × 10 tune seeds × binpack |
| Held-out matrix | 1,920/1,920 rows; 8 × 2 × 3 × 10 test seeds × 4 policies |
| Seeds | tune `0–9`; test `1000–1009`; disjoint |
| Key integrity | no missing, duplicate, or unexpected experiment keys |
| Numeric integrity | required values finite; source values retain full serialization precision |
| Policy pairing | identical retained trace metadata for all four policies in every trace |
| Full trace regeneration | all 480 traces regenerated twice; node IDs/capacities, jobs, order, demands, arrivals, durations, and parameters matched deterministically |
| Raw metric arithmetic | all 2,400 calibration + held-out rows reconciled |
| Held-out raw SHA-256 | `8f46c90029b46ce23c23b22ca1b45a388628830bcea80d3b359b066a3090fcb1` |

Code inspection and a separate hand-worked event ledger verified completion-
before-arrival ordering, release-before-dispatch, inclusive horizon events,
snapshot-before-drain semantics, exact-node resource release, FIFO scan with
backfilling, permanent-infeasibility separation, interval-integrated
time-weighted metrics, nearest-rank P95, and undefined empty samples. The
legacy static core files have no diff from the pre-Phase-4 parent; the temporal
runner is additive and the admission-only runner remains available. The
hand-worked ledger independently produced waits `[0,4]`, turnaround `[5,6]`,
slowdown `[1,3]`, horizon throughput `1/6`, time-weighted CPU/RAM utilization
`0.6/0.4`, time-weighted queue length `4/6`, makespan `7`, and drained
throughput `2/7`.

## Claim reconciliation

Differences below are independent minus reported. Reported display values pass
when the full-precision value rounds to them. Original interval claims are
marked corrected even when their scientific conclusion survives.

| Claim | Reported | Independent full precision | Difference | Result |
|---|---:|---:|---:|---|
| Calibration runs | 480 | 480 | 0 | PASS |
| Held-out runs | 1,920 | 1,920 | 0 | PASS |
| Final tests | 170 | 171 | +1 | CORRECTED (audit regression test added) |
| High Tetris throughput delta | +0.256 | +0.256375000 | +0.000375 | PASS (rounding) |
| High Tetris relative throughput | +1.38% | +1.381116880% | +0.001116880 pp | PASS (rounding) |
| High Tetris backlog delta | -9.41 | -9.406250000 | +0.003750 | PASS (rounding) |
| High Tetris P95-wait delta | +0.529 | +0.528720346 | -0.000279654 | PASS (rounding) |
| High Tetris active-node delta | +1.19 | +1.194212795 | +0.004212795 | PASS (rounding) |
| Medium Tetris throughput delta | -0.036 | -0.035875000 | +0.000125 | PASS (rounding) |
| Medium Tetris P95-wait delta | +0.690 | +0.690466436 | +0.000466436 | PASS (rounding) |
| Medium Tetris active-node delta | +1.62 | +1.619770112 | -0.000229888 | PASS (rounding) |
| Low Tetris throughput delta | 0.000 | 0.000000000 | 0 | PASS |
| Low Tetris P95-wait delta | +0.127 | +0.126720863 | -0.000279137 | PASS (rounding) |
| Low Tetris active-node delta | +5.92 | +5.918121767 | -0.001878233 | PASS (rounding) |
| High hybrid throughput delta | +0.162 | +0.161750000 | -0.000250 | PASS (rounding) |
| High hybrid active-node delta | +0.86 | +0.856632866 | -0.003367134 | PASS (rounding) |
| Highest high-load throughput policy | Tetris | Tetris (`18.81925`) | — | PASS |
| Clear high-load Tetris families | RAM-heavy, bimodal, tiny/large, drift, adversarial | same list under seed blocking | — | PASS |
| Uncertain high-load families | balanced, CPU-heavy, prediction error | same list under seed blocking | — | PASS |
| EWMA evaluated temporally | no | policy set is exactly binpack, spread, Tetris, hybrid | — | PASS |
| Low load cleared | cleared | near-zero, not universally zero: 1/160 binpack and 2/160 Tetris traces retain backlog | — | CORRECTED |

Fewer queued jobs and higher drained-job P95 wait at high load both verify.
They measure different summaries and time boundaries; the data do not by
themselves establish starvation or a causal fairness mechanism.

Independent policy rankings are shown best to worst for each metric. Ties are
stated explicitly.

| Load | Throughput (higher) | P95 wait (lower) | Backlog (lower) | Active nodes (lower) |
|---|---|---|---|---|
| low | all policies tie | binpack, spread, hybrid, Tetris | spread, binpack, Tetris = hybrid | binpack, hybrid, Tetris, spread |
| medium | hybrid, binpack, spread, Tetris | binpack, spread, hybrid, Tetris | hybrid, binpack, spread, Tetris | binpack, hybrid, Tetris, spread |
| high | Tetris, hybrid, spread, binpack | binpack, hybrid, spread, Tetris | Tetris, hybrid, spread, binpack | binpack, hybrid, Tetris, spread |

## Original and seed-blocked intervals

The original intervals below reproduce the superseded all-pairs calculation.
The release intervals use the approved ten seed blocks. Means are unchanged.

| Comparison and metric | Mean delta | Original all-pairs 95% CI | Seed-blocked 95% CI |
|---|---:|---:|---:|
| Tetris, low throughput | 0.000000 | `[0.000000, 0.000000]` | `[0.000000, 0.000000]` |
| Tetris, low P95 wait | +0.126721 | `[+0.077321, +0.176121]` | `[+0.089220, +0.164221]` |
| Tetris, low backlog | +0.006250 | `[-0.006094, +0.018594]` | `[-0.007888, +0.020388]` |
| Tetris, low active nodes | +5.918122 | `[+5.626497, +6.209747]` | `[+5.434460, +6.401783]` |
| Tetris, medium throughput | -0.035875 | `[-0.062940, -0.008810]` | `[-0.052961, -0.018789]` |
| Tetris, medium P95 wait | +0.690466 | `[+0.544337, +0.836596]` | `[+0.500985, +0.879947]` |
| Tetris, medium backlog | +2.193750 | `[+0.443175, +3.944325]` | `[+1.396056, +2.991444]` |
| Tetris, medium active nodes | +1.619770 | `[+1.505206, +1.734334]` | `[+1.460806, +1.778734]` |
| Tetris, high throughput | +0.256375 | `[+0.192015, +0.320735]` | `[+0.201024, +0.311726]` |
| Tetris, high P95 wait | +0.528720 | `[+0.328981, +0.728460]` | `[+0.303648, +0.753793]` |
| Tetris, high backlog | -9.406250 | `[-12.643108, -6.169392]` | `[-11.642957, -7.169543]` |
| Tetris, high active nodes | +1.194213 | `[+1.111890, +1.276535]` | `[+1.110424, +1.278002]` |
| Spread, high throughput | +0.114750 | `[+0.060193, +0.169307]` | `[+0.072356, +0.157144]` |
| Hybrid, high throughput | +0.161750 | `[+0.112015, +0.211485]` | `[+0.127041, +0.196459]` |
| Hybrid, high P95 wait | +0.314575 | `[+0.155645, +0.473505]` | `[+0.072161, +0.556989]` |
| Hybrid, high backlog | -6.612500 | `[-9.344702, -3.880298]` | `[-8.778433, -4.446567]` |
| Hybrid, high active nodes | +0.856633 | `[+0.771457, +0.941809]` | `[+0.791506, +0.921759]` |

High-load Tetris family throughput intervals after seed blocking are:

| Family | Mean delta | Seed-blocked 95% CI | Interpretation |
|---|---:|---:|---|
| balanced | +0.090 | `[-0.026, +0.206]` | uncertain |
| CPU-heavy | +0.022 | `[-0.074, +0.118]` | uncertain |
| RAM-heavy | +0.114 | `[+0.049, +0.179]` | positive |
| bimodal | +0.128 | `[+0.051, +0.205]` | positive |
| tiny/large | +0.261 | `[+0.114, +0.408]` | positive |
| drift | +0.810 | `[+0.549, +1.071]` | positive |
| prediction error | -0.004 | `[-0.046, +0.038]` | uncertain |
| adversarial | +0.630 | `[+0.333, +0.927]` | positive |

## Resume-safe claims

- Built a deterministic event-driven Python scheduler simulator reproducing a
  pinned HashiCorp Nomad CPU/RAM fit score, with Poisson arrivals, job
  completion/resource release, FIFO-scan backfilling, horizon/drain metrics,
  and validation by 171 tests covering scoring, lifecycle invariants, and
  seed-block statistics.
- Executed 1,920 paired held-out temporal runs across 48
  workload/cluster/load cells, finding that Tetris increased overload
  throughput by 1.38% (paired absolute-delta 95% CI +0.201 to +0.312
  jobs/time unit) while using 1.19 more time-weighted active nodes and
  increasing P95 wait by 0.53 simulated time units.
- Quantified a load-dependent result: resource-shape alignment added no
  low-load throughput, slightly reduced near-saturation throughput overall,
  and improved overload throughput most on mixed, drifting, and adversarial
  request shapes.

## Reproducibility evidence

- Existing environment: 171 passed, 0 failed, 0 errors, 0 skipped, 0 xfailed.
- Fresh isolated Python 3.12 environment: 171 passed, 0 failed, 0 errors,
  0 skipped, 0 xfailed.
- Pre-Phase-4 worktree: 127 passed; original Phase 4 release worktree: 170
  passed, establishing 43 Phase 4 tests. The audit adds one seed-block
  statistics regression test for a final total of 171.
- Legacy smoke SHA-256, identical across two fresh runs:
  `83b387a16b8155c52ccf870f305710618082ae48e2599fba92cbe395068c00c3`.
- Temporal smoke SHA-256, identical across two fresh runs:
  `9de34cc7ab09556a109cfbd83cbac9dd4a6ed6760a74ab48e87d66ed454354ab`.
- Before correction, all 11 committed canonical artifacts regenerated
  byte-for-byte from the committed analyzer, proving the prior release was
  reproducible even though its aggregate interval method required correction.
- The full fresh calibration and held-out rerun reproduced the canonical raw
  CSVs byte-for-byte. The corrected 11 canonical artifacts also regenerated
  byte-for-byte with the audited lock.
- Independent committed-raw and fresh-raw statistics JSON outputs were
  byte-identical with SHA-256
  `08921c580e94566bf916be51d53876e576fea1417d3b5cdcb89eebb5fae2a076`.

Corrected canonical artifact SHA-256 values:

| Artifact | SHA-256 |
|---|---|
| `cell_summary.csv` | `3376d3e64f0c65c14febcec7382c12b25d054c793d727fde48dd61abbefd9440` |
| `family_load_summary.csv` | `14dd800d8f859a5b0ef9f524f40b7cef32d4bca35d147c09903d8678895dafb0` |
| `overall_load_summary.csv` | `b514a401ae57fa17271cd6245526ef723962b66f3dcb01f8eeac056c9a0e7e8b` |
| `paired_vs_binpack.csv` | `e475aafcd9ffb9c3cfc4640c6f5712be18dba179c55b9ceea2db25087f45353b` |
| `paired_family_load_vs_binpack.csv` | `b6c9a2c0e3b5f0dd6e3c9c01fcad34519474c5a9ca0e115936aeb23e4011595a` |
| `paired_overall_load_vs_binpack.csv` | `57913aaf6396dbfe6bc174855edd8eee537ca6ec91f8fe9df0c8674a82c572c7` |
| `manifest.json` | `8d18d0bd49c08ae29f954090c3c28b806c29246ecd81cf391155b1cb83563ea4` |
| `plots/backlog_by_load.png` | `5b1005c1ca78bcda4f39b5b6e429bb0d0fc5cd112d9be92c1f73ea2111bda6c4` |
| `plots/p95_wait_by_family_load.png` | `1cd7a083060cf5bba4c860d63ec8c1a8686de8e6a6cd8278d29b79062d90ff82` |
| `plots/throughput_by_family_load.png` | `d28ba6a9e2f7b00c08f5e82267284778ab90cd15702cfbe2c612a8ee2bc87c3a` |
| `plots/throughput_vs_active_nodes.png` | `6525585eb3d78b7f01530348b75afe72914efe3000bb882803ed5b262d83b2a2` |

## Audit commands

```bash
python3 -m venv .venv-audit
source .venv-audit/bin/activate
python -m pip install -r requirements-lock.txt
python -m pytest
python -m compileall -q sim scripts tests
ruff check --select F,E9 sim scripts tests
python scripts/run_smoke_experiment.py
python scripts/run_temporal_smoke.py
python scripts/analyze_temporal.py
python scripts/verify_temporal_stats.py
```

The full-matrix commands are documented in `README.md`. The family shards are
restart points and must be merged only after all eight complete.

## Remaining limitations

- The environment lock records the audited Python 3.12/Linux stack. Other
  platforms may render different PNG bytes even when numeric CSVs agree.
- Results come from synthetic Poisson arrivals, bounded durations, CPU/RAM
  resources, one finite horizon, and one FIFO-scan/backfill queue discipline.
- There is no trace replay, production execution, long-run stability proof,
  topology/device/network/disk model, priority, preemption, migration,
  autoscaling, failure model, or global placement optimum.
- Confidence intervals describe the selected seeded synthetic design. Ten seed
  blocks are a limited inferential sample, and family comparisons are
  exploratory without multiplicity adjustment.
- EWMA/future-fit remains a Phase 3 admission-only result and was not evaluated
  in the temporal matrix.
