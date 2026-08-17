# Experiment report: workload-aware extensions to Nomad's fit score

Phase 3 of nomad-placement-lab. Protocol, formulas, and metric
definitions: `DESIGN.md`. Raw data: `results/matrix/*.csv`. Plots:
`results/matrix/plots/*.png`. Reproduce with:

This report is retained as the historical **Phase 3 admission study**. The
completed lifecycle/throughput study is in `TEMPORAL_EXPERIMENT_REPORT.md`.

```bash
python3 -m pytest                                             # 170 current tests (127 at Phase 3 freeze)
python3 scripts/run_matrix.py tune_cell {0..7}                # tuning shards (tune seeds 0-9)
python3 scripts/run_matrix.py tune_select                     # -> tuning.csv, tuned.json
python3 scripts/run_matrix.py val                             # -> val.csv (seeds 100-104)
python3 scripts/run_matrix.py final_fam <family>  # x8        # held-out shards (seeds 1000-1009)
python3 scripts/run_matrix.py final_merge                     # -> final.csv
python3 scripts/run_matrix.py sensitivity {0,1,2}             # -> sensitivity_*.csv
python3 scripts/analyze_matrix.py                             # tables + plots from CSVs
```

Scope reminder: the Phase 3 runner is admission-only (no durations, no
departures, no queue). Every number below is a placement/admission
result on a 30-node cluster, not completed-job throughput.

## 1. What was tuned, on what

Tuned on seeds 0–9, mixed-shape cells only ({bimodal, drift} × {homog,
hetero} × {med, high}); selection = highest mean per-seed paired
admission delta vs. exact Nomad binpack:

| family | selected | mean paired delta (tune cells) |
|---|---|---:|
| hybrid (C) | alpha = 0.1 (beta = 0.9) | +0.0360 |
| balance | lam = 0.8 | +0.0273 |
| wmatch | lam = 0.8 | +0.0009 |
| D1 cheap | alpha 0.0, beta 0.8, gamma 0.2 | +0.0369 |
| D2 capacity | alpha 0.0, beta 0.8, gamma 0.2 | +0.0363 |

Two tuning observations that shaped everything downstream. First, the
unconstrained best config in the whole D grid was the corner
(alpha 0, beta 1, gamma 0) — i.e., **pure Tetris alignment**, +0.0379.
Second, tuning deltas were monotone toward high beta: nearly all of the
achievable gain comes from the alignment term, and gamma adds at most
noise-level improvement beyond it (gamma sweep at beta = 0.8:
+0.0346 / +0.0350 / +0.0369 for gamma 0 / 0.1 / 0.2).

## 2. Held-out results (seeds 1000–1009, all 48 cells)

Mean per-seed paired admission-rate delta vs. Nomad binpack, pooled per
family (60 paired observations per cell entry; full CIs in
`comparison_admission.csv`, per-cell breakdown in
`comparison_by_cell.csv`):

| family | spread | tetris | hybrid | balance | wmatch | d1_cheap | d2_capacity |
|---|---:|---:|---:|---:|---:|---:|---:|
| balanced | +0.0026 | +0.0024 | +0.0025 | +0.0007 | +0.0000 | +0.0023 | +0.0029 |
| cpu_heavy | −0.0004 | −0.0023 | −0.0018 | +0.0023 | −0.0001 | −0.0000 | −0.0011 |
| ram_heavy | +0.0036 | +0.0070 | +0.0080 | +0.0025 | +0.0002 | +0.0078 | +0.0081 |
| **bimodal** | +0.0051 | **+0.0162** | +0.0150 | +0.0077 | −0.0000 | +0.0138 | +0.0137 |
| **tiny_large** | +0.0088 | **+0.0193** | +0.0169 | +0.0048 | +0.0005 | +0.0167 | +0.0172 |
| **drift** | +0.0257 | **+0.0367** | +0.0339 | +0.0312 | +0.0004 | +0.0352 | +0.0362 |
| pred_error | +0.0018 | −0.0001 | +0.0000 | +0.0027 | −0.0002 | +0.0008 | +0.0001 |
| **adversarial** | +0.0215 | +0.0193 | +0.0185 | +0.0140 | +0.0003 | +0.0212 | **+0.0222** |

Statistical status of the headline cells: on all four mixed-shape
families, tetris/hybrid/D1/D2 have 95% CIs excluding zero and paired
records of 20 wins / 0 losses / 40 ties (ties are low-load cells where
every policy admits 100%). Example: bimodal tetris +0.0162,
CI [+0.0092, +0.0232]; drift tetris +0.0367, CI [+0.0231, +0.0503].

Failure cases, reported with equal prominence:

- **cpu_heavy is a real (small) loss for alignment-based policies**:
  tetris −0.0023 CI [−0.0050, +0.0004], 6 wins / 12 losses / 42 ties.
  Not statistically significant, and within the pre-registered 1-point
  tolerance, but the sign is consistent: when every job has the same
  shape, steering by shape only forfeits a little packing quality.
- **wmatch (the simple mismatch-penalty heuristic) does nothing**:
  every family within ±0.0005. Its λ·mismatch term is too weak to
  change decisions before the binpack term dominates, and making λ
  large enough to matter (0.8 was the tuned max) still moved almost
  nothing. The simple formula from the project notes is honestly
  reported as a null result.
- **future-fit alone is nearly inert**: the ff_only ablation is ≈0 on
  everything except adversarial (+0.0185), where any shape-sensitivity
  helps. Combined D variants track pure tetris within ~0.003 everywhere
  — the gamma term never separates from noise once beta is present.

## 3. Secondary metrics (held-out, all runs pooled)

Timing columns below are snapshots from the original experiment environment.
They are useful as a coarse within-run comparison but are not portable
benchmarks and are excluded from canonical resume claims.

| policy | active nodes | mean CPU util | mean RAM util | free imbalance | stranded frac | mean latency | × binpack |
|---|---:|---:|---:|---:|---:|---:|---:|
| binpack | 23.0 | 0.683 | 0.421 | 0.381 | 0.230 | 64 µs | 1.00× |
| spread | 30.0 | 0.665 | 0.394 | 0.391 | 0.125 | 89 µs | 1.39× |
| tetris | 29.4 | 0.668 | 0.413 | 0.325 | 0.081 | 91 µs | 1.42× |
| hybrid | 28.2 | 0.670 | 0.423 | 0.314 | 0.087 | 112 µs | 1.75× |
| d1_cheap | 27.2 | 0.669 | 0.426 | 0.312 | 0.091 | 147 µs | 2.30× |
| d2_capacity | 27.3 | 0.668 | 0.425 | 0.312 | 0.088 | 203 µs | 3.18× |

The admission gains have a price that admission rate alone hides:
**alignment-driven policies consolidate much less**. Binpack leaves ~7
of 30 nodes completely idle on average; tetris lights up ~29. If
active-node efficiency (e.g., autoscaling down idle nodes) matters,
binpack remains preferable on stationary workloads. In exchange, the
alignment policies strand far less capacity (0.08 vs. 0.23 of total
capacity stuck on nodes that cannot fit even the smallest job class)
and leave residuals with lower CPU/RAM imbalance. All latencies are
well inside the pre-registered 5× bound.

## 4. Robustness

- **Distribution shift (drift)** is where workload-shape sensitivity
  helps most (+3.4 to +3.7 points for tetris/hybrid/D) — but note this
  gain does not come from the adaptive EWMA profile: stateless tetris,
  which adapts to nothing, captures all of it. Reacting to each job's
  own shape is enough; predicting the mix adds nothing measurable.
- **Corrupted prediction signal (pred_error)**: no policy collapses;
  deltas are ±0.001. With the tuned weights (gamma 0.2) the fallback
  ON/OFF ablation is indistinguishable (+0.0000 both) — the fallback
  mechanism works as designed (fresh profiles start at confidence 0;
  erratic streams cap confidence, unit-tested), but at gamma ≤ 0.2 the
  future-fit term is too small to do damage even uncorrected. The
  mechanism's value would only be testable at larger gamma, which
  tuning never selects.
- **Sensitivity plateaus** (no knife-edges): gamma sweep at beta 0.8
  varies within 0.0023 across gamma ∈ {0, 0.1, 0.2}; EWMA decay sweep
  is flat (+0.0347 … +0.0369 across eta 0.02–0.3); hybrid alpha curve
  declines smoothly from alpha 0.1 toward pure binpack
  (`plots/tuning_curves.png`).
- **Generalization**: tune-cell deltas (+0.036) slightly overstate but
  correctly rank held-out mixed-shape results (+0.014 to +0.037);
  no sign flips between tune and test on any family × headline policy.

## 5. Conclusions (per the pre-registered decision rule)

1. **The alignment term is the entire story.** Cosine alignment between
   job demand and node free resources (Tetris-style) produces the only
   statistically repeatable held-out gains on mixed-shape workloads:
   +1.4 to +3.7 admission points, 20/0 paired wins, CIs excluding zero,
   at 1.4× baseline latency, in one explainable sentence: *"send
   CPU-heavy jobs to CPU-abundant nodes."*
2. **The recommended policy is `hybrid` (alpha 0.1 binpack +
   0.9 tetris)** — statistically indistinguishable from pure tetris on
   every family (max gap 0.003, CIs overlap almost entirely) while
   retaining a nonzero packing term that recovers slightly more RAM
   utilization and ~1 fewer active node. If zero tunables are preferred,
   pure tetris is defensible; if consolidation matters more than
   admission, keep binpack.
3. **The workload-aware machinery (EWMA profile + future-fit) is not
   justified by these results.** D1/D2 never beat pure alignment on
   held-out data; the profile-driven term is inert at the weights tuning
   selects and its flagship scenario (drift) is fully captured by the
   stateless alignment term. The honest summary of the project's central
   hypothesis: *the experiments showed that a stateless shape-matching
   term captures the available gains, and Nomad's convex bin-pack score
   already handles stationary workloads well; explicit workload
   prediction added no measurable value in this admission-only setting.*
4. **A neural scorer is not justified.** The gap it would need to beat
   (hybrid vs. best-anything: ≤ 0.3 points on any family) is smaller
   than seed noise on most cells, and the deterministic winner is
   already cheap and explainable. Rollout-teacher + MLP work should wait
   until durations/departures exist, where lookahead has real signal.
5. **Follow-up status**: Phase 4 added durations, queueing, release, and
   throughput for the four primary policies. Remaining follow-ups include
   temporal EWMA sensitivity, strict-FIFO sensitivity, a tiny exact-packing
   solver for optimality gaps, and an active-node-aware objective.

## 6. Historical Phase 3 resume bullets

- Built a deterministic Python simulator reproducing HashiCorp Nomad's CPU/RAM
  binpack and spread fit scoring, with immutable cluster state, seeded workload
  generation, and 127 tests at the Phase 3 freeze covering scoring,
  feasibility, determinism, and experiment invariants.
- Evaluated 11 placement policies and ablations across 48 workload/cluster/load
  configurations and 10 held-out seeds, finding that Tetris-style resource
  alignment improved admission by 1.6–3.7 percentage points on four mixed-shape
  workloads.
- Quantified the admission–consolidation trade-off (29.4 active nodes for
  Tetris vs. 23.0 for binpack on 30-node clusters) and found that EWMA-based
  demand prediction added no material gain over the simpler stateless heuristic.

*(No claim of improving production Nomad; all results are simulation-
scope, admission-only, and stated with their failure cases.)*

These are retained for provenance. Current approved bullets and the complete
170-test evidence mapping are maintained in `docs/RESUME_EVIDENCE.md`.
