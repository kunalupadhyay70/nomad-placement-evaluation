# Methodology Correction Report

This report documents a correction pass over `nomad-placement-lab`'s
Phase Two implementation. It does not touch the verified Phase One
scoring policies (`sim/policies.py`) -- see `PHASE1_REPORT.md` for that
work, which remains unchanged. It also does not implement Nomad's full
scheduler, affinity/anti-affinity, devices, networking, candidate
sampling, preemption, job completion, migration, a workload-aware policy,
or machine learning -- none of that was in scope for this correction.

## 1. Problems identified in the previous version

Inspection of the repository (all of `sim/`, all of `tests/`, `README.md`,
`PHASE1_REPORT.md`, and `results/baseline_results.json`) before any
changes found:

1. **Workload model conflated two independent dimensions.**
   `sim/workload.py`'s `DEFAULT_SHAPES` treated
   `small, medium, large, cpu-heavy, ram-heavy` as five points on one
   axis. `small/medium/large` describe total resource magnitude;
   `cpu-heavy/ram-heavy` describe CPU:RAM ratio. There was no way to
   generate, say, a "small CPU-heavy" job, so size and shape effects were
   inseparable in any result.
2. **The "fragmentation" metric was misnamed and its README interpretation
   was mathematically inconsistent with its own formula.** The formula
   (`1 - max_free/total_free`) is correctly documented in the code's own
   module docstring as "higher = free capacity scattered thinly across
   many nodes." But the previous `README.md` claimed: "binpack's higher
   fragmentation ... is consistent with its design goal: it deliberately
   concentrates usage on fewer nodes, so a larger share of the remaining
   free capacity sits on a small number of nodes" -- i.e. it read a higher
   value as *more concentrated*, which is the reverse of what the formula
   computes. This was a direct contradiction between the metric's
   definition and its stated interpretation.
3. **No operational schedulability metrics existed.** Nothing in the
   previous version answered "can the cluster, as it currently stands,
   still fit a job like X" -- only aggregate utilization/imbalance/
   fragmentation, none of which directly answers that question.
4. **Tie-breaking used `node_id`, and `node_id` was not independent of
   cluster structure.** `generate_cluster` assigns
   `node_id = f"node-{i:03d}"` in the same loop iteration that draws each
   node's random capacity, so `node_id` order is cluster creation order by
   construction. Sorting ties by `node_id` was therefore sorting by
   creation order while appearing to be an arbitrary naming convention.
5. **A single 24-node/400-job/seed-42 run was presented as a "baseline"
   with causal conclusions** ("spread is better than bin-pack... bin-pack
   concentrates load onto a few nodes early, which exhausts those nodes'
   capacity ... sooner"), despite being one deterministic configuration
   with no repetition, no controlled workload/arrival-order variation, and
   no decision-trace evidence offered for the causal claim.

Baseline before any changes: **38/38 tests passing.** All 38 were valid
tests of the (then-current) implementation; none were deleted, only
extended or, in one case, corrected to match a fixed behavior (see
section 3).

## 2. Size/shape model correction

`sim/workload.py` was rewritten around two independent tables:

- `SHAPES`: `balanced` (0.50/0.50), `cpu_heavy` (0.80/0.20), `ram_heavy`
  (0.20/0.80) CPU/RAM share.
- `SIZES`: `small`=10, `medium`=25, `large`=40 (total `cpu + ram`).

`job_resources(size_class, shape)` returns `(size * cpu_share, size *
ram_share)`. All 9 combinations are enumerated in `CANONICAL_CLASSES`.
The old `generate_jobs(n, seed)` (random n, random per-shape magnitude
via `rng.uniform`) was replaced with
`generate_workload(size_shape_counts, seed, shuffle=True)`, which takes
**exact** requested counts per `(size_class, shape)` pair, assigns
deterministic job IDs (`job-<size>-<shape>-<index>`) before shuffling,
and shuffles arrival order with a local `random.Random(seed)` instance
that never touches the global `random` module state (tested directly:
`test_generate_workload_does_not_touch_global_random_state`).

`sim/models.py`'s `Job` gained a `size_class: str = "unspecified"` field
alongside the existing `shape: str` field (now populated with canonical
shape names, not the old conflated labels). Both default to
`"unspecified"` so pre-existing ad hoc `Job(...)` calls in tests continue
to work unchanged. `Job` remains a frozen, slotted dataclass; immutability
was not weakened.

`generate_cluster` (node capacity generation) was not changed -- it was
not part of the conflated-axis problem.

## 3. Dispersion-metric correction

`cpu_fragmentation` / `ram_fragmentation` were renamed to `cpu_dispersion`
/ `ram_dispersion` in `sim/metrics.py`'s `ClusterMetrics`. The formula is
unchanged:

```text
D_r = 1 - (max_i free_r(node_i) / sum_i free_r(node_i))
```

The module docstring now states explicitly: lower = residual free
capacity concentrated on one node; higher = spread thin across many
nodes; **a higher value is not inherently worse**, and this metric alone
does not establish whether residual capacity is usable. The zero-total-
free-capacity edge case is defined as `0.0` by convention (documented and
tested in `test_dispersion_zero_total_free_capacity_is_defined_as_zero`),
not left as an implicit division-by-zero risk.

A second new field pair, `cpu_free_fraction_gap` / `ram_free_fraction_gap`
(max free-fraction minus min free-fraction across nodes), was added to
give a second, distinct "how uneven is the cluster" reading that is not
the dispersion formula.

All JSON output, plot labels, and README text were updated to use
`dispersion`; no field name containing `fragmentation` remains (tested:
`test_metrics_field_names_use_dispersion_not_fragmentation`).

## 4. Tie-breaking investigation

Traced `node_id` generation in `sim/workload.py::generate_cluster`:
`node_id = f"node-{i:03d}"` is assigned inside the same `for i in
range(n_nodes)` loop that draws `total_cpu`/`total_ram` from the seeded
RNG for that same index `i`. So `node_id` numeric suffix equals list
index equals creation order, always, by construction -- not by
coincidence of a particular seed. Sorting ties by `node_id` string
(`sorted` on `f"node-{i:03d}"` strings) is therefore equivalent to
sorting by creation order, which is a property of the cluster generator,
not an arbitrary tiebreak independent of cluster structure.

`sim/scheduler.py::place` now tracks each feasible node's index in the
input `cluster` sequence and, among nodes with exactly the winning score
(exact float equality -- every candidate is computed by the same
deterministic pipeline, so no tolerance is warranted), picks the smallest
index. This is independent of `node_id` string content: renaming nodes
cannot change the outcome, only reordering the `cluster` sequence can.
Both properties are tested directly
(`test_tie_result_unaffected_by_renaming_node_ids`,
`test_tie_winner_changes_when_sequence_is_reordered`).

`PlacementResult` gained a `tied_candidate_count` field (1 = no tie, N =
N-way tie at the winning score, 0 = no placement), and
`PolicyRunResult.score_tie_count` (in `sim/experiment.py`) counts how many
job decisions in a run had `tied_candidate_count > 1`. In the corrected
smoke experiment (below), all three policies recorded `score_tie_count =
0` -- with heterogeneous, continuously-valued node capacities, exact
score ties are rare in this configuration; the mechanism was verified
directly with hand-constructed identical-node tests instead
(`tests/test_scheduler.py`).

## 5. New operational metrics

Added to `sim/metrics.py`: `nodes_fitting(cluster, cpu_demand, ram_demand)`
and `compute_class_fit_counts(cluster, class_resources)`, which count, per
canonical class, how many nodes in a given cluster state currently have
enough free CPU *and* RAM to fit one more such job.

Added to `sim/experiment.py`'s `PolicyRunResult`: `placed_by_size`,
`rejected_by_size`, `placed_by_shape`, `rejected_by_shape`,
`placed_by_size_and_shape`, `rejected_by_size_and_shape` (all
serializable `Dict[str, int]`, tested to reconcile with `jobs_placed`/
`jobs_failed` totals), `nodes_fitting_class` (final-cluster per-class fit
counts), and `additional_jobs_placeable` (a greedy repeated-placement
estimate: apply the same policy repeatedly to a scratch copy of the final
cluster, per class, until no feasible node remains -- deterministic,
documented as not optimal packing, and classes are evaluated
independently of each other).

None of these are documented or claimed as a complete or perfect
schedulability measurement.

## 6. Updated smoke configuration

`scripts/run_baseline.py` (previous) is superseded by
`scripts/run_smoke_experiment.py` (corrected). Configuration:

- 24 nodes, seed 42 (cluster generation unchanged from before)
- 20 jobs per canonical (size_class, shape) class, all 9 classes, 180
  jobs total, shuffled arrival order (seed 42)
- Policies: `binpack`, `spread`, `tunable-base-4`
- Every policy receives the identical cluster, identical jobs, identical
  arrival order, each starting from a fresh, unmodified cluster (tested:
  `tests/test_experiment.py`)

The per-class job count (20, chosen after inspecting 8/12/15/18/20/45)
was picked so the run exercises both outcomes -- most jobs placed, a
meaningful minority rejected -- rather than either trivially succeeding
(low counts) or exhausting the cluster so completely that every
operational metric reads zero (the original 45-per-class figure did
this: every `nodes_fitting_class` value was 0 for every policy, which is
a legitimate result but not a useful smoke-test illustration).

Full results, including full per-job decision traces, final node states,
and every metric listed in the README, are written to
`results/smoke_results.json`. The previous `results/baseline_results.json`
was copied (not deleted or overwritten) to
`results/legacy_baseline_v1_results.json` and is marked superseded in the
README; it used the flawed size/shape-conflated workload and the
"fragmentation" naming and should not be cited as current.

## 7. Corrected results

| Policy | Placed | Failed | Score ties | Final CPU util | Final RAM util | CPU dispersion | CPU free-gap |
|---|---:|---:|---:|---:|---:|---:|---:|
| binpack | 173 | 7 | 0 | 0.911 | 0.723 | 0.717 | 0.568 |
| spread | 172 | 8 | 0 | 0.857 | 0.661 | 0.834 | 0.564 |
| tunable-base-4 | 175 | 5 | 0 | 0.916 | 0.725 | 0.689 | 0.568 |

(Out of 180 jobs offered per policy; full breakdowns in
`results/smoke_results.json`.)

## 8. Complete test results

Before correction: `38 passed in 0.12s` (recorded prior to any edits).

After correction, full suite (73 tests: 38 original scope extended/fixed
+ 35 new):

```text
$ PYTHONPATH=. python3 -m pytest tests/ -v
...
73 passed in 0.18s
```

New/changed test files: `tests/test_workload.py` (rewritten for
size/shape separation, 17 tests), `tests/test_metrics.py` (rewritten for
dispersion + schedulability, 18 tests), `tests/test_scheduler.py` (tie
test corrected + 4 new tie tests, 15 tests total), `tests/test_experiment.py`
(new, 6 tests). `tests/test_policies.py` (14 tests, Phase One, unchanged).

## 9. Claims removed or weakened

- Removed: "Spread is better than bin-pack" framing and any implication
  that one policy generally outperforms another.
- Removed: the causal claim that "bin-pack concentrates load onto a few
  nodes early, which exhausts those nodes' capacity for the specific
  resource shapes in this workload sooner" -- this was asserted without
  decision-trace evidence and is not repeated in the corrected README.
  The corrected README instead lists workload composition, arrival order,
  heterogeneous node capacities, and tie-breaking as plausible
  contributing factors, explicitly requiring controlled experiments to
  identify any actual mechanism.
- Corrected (was mathematically backwards): "binpack's higher
  fragmentation ... is consistent with ... free capacity being
  concentrated on fewer nodes." Replaced with the metric-consistent
  reading: a higher dispersion value means residual free capacity was
  *more spread out*, not more concentrated, and this alone does not
  establish whether that residual state is more or less useful --
  operational per-class numbers are used instead for any concrete claim.
- Weakened: "24 nodes, 400 jobs, seed 42" was called a "baseline
  experiment" implying general validity. It is now referred to
  consistently as a single-seed smoke/integration experiment, with an
  explicit non-generalization statement in both the README and the
  results JSON's `limitation` field.
- Downgraded from a stated fact to an explicitly hedged observation: "In
  this particular seed and configuration, X placed more jobs than Y,"
  rather than an unqualified comparison.

## 10. Did the numerical conclusion change?

**Yes, the direction reversed.** The previous (flawed) baseline run found
`spread` placing more jobs than `binpack` (277 vs. 250 out of 400
offered, +27 for spread). The corrected smoke run, with independent
size/shape workload generation, corrected dispersion metric, and
corrected tie-breaking, found `binpack` and `tunable-base-4` placing
slightly *more* jobs than `spread` (173 and 175 vs. 172 out of 180
offered, spread trailing by 1-3 jobs). Both differences are small
relative to the totals involved, and neither run -- old or corrected --
used more than one seed, so neither establishes a general policy
advantage in either direction. The reversal itself is reported here
factually, without claiming the corrected direction is "the right
answer" -- that would require the multi-seed matrix described in
"Remaining limitations" below, which has not been run.

One consistent-looking pattern across old and corrected runs: `spread`'s
final-state operational schedulability (`additional_jobs_placeable`) was
substantially higher than `binpack`'s for smaller job classes in the
corrected run (e.g. `small_ram_heavy`: 16 for spread vs. 2 for binpack).
This is reported as an observation about this one run's final states, not
as a general property of the policies.

## 11. Remaining limitations

- Single seed, single cluster, single workload composition, single
  arrival order. No repetition, no variance estimate, no significance
  testing.
- `additional_jobs_placeable` is a greedy, single-class-at-a-time
  estimate, not an optimal packing bound, and does not model placing a
  mix of classes together.
- `score_tie_count` was 0 in the corrected smoke run; the tie-breaking
  fix is verified by targeted unit tests with hand-constructed ties, not
  by the smoke run itself (which happened not to produce any with
  continuously-valued heterogeneous node capacities).
- Node capacity generation (`generate_cluster`) is still a single
  uniform-random model; it was not varied or investigated for its own
  potential correlations beyond the `node_id`/creation-order issue fixed
  here.
- No workload-aware policy exists, and none should be built from a
  single-seed result.

## 12. Exact next Phase Three plan

Not started; explicitly out of scope for this correction pass. Planned
shape:

1. **Multi-seed matrix.** Run every (policy x workload-mix x
   arrival-order x cluster-type) combination across e.g. 30+ independent
   seeds.
2. **Controlled workload mixes.** Vary the 9-class distribution
   deliberately (e.g. size-skewed, shape-skewed, uniform) rather than
   using only the uniform split from the smoke test.
3. **Controlled arrival orders.** Compare shuffled vs. size-sorted vs.
   shape-grouped arrival within the same workload mix.
4. **Homogeneous and heterogeneous clusters.** Add a fixed-capacity
   cluster generator alongside the existing randomized-capacity one.
5. **Policy win counts.** Report, per configuration, how many of the N
   seeds each policy "won" on jobs-placed (and on other metrics), not
   just one run's totals.
6. **Means and standard deviations** across seeds for every metric in
   `ClusterMetrics` and `PolicyRunResult`.
7. **Only after that matrix is built and trustworthy:** design and
   evaluate a workload-aware policy against the same matrix, so any claim
   of improvement is measured the same way the baseline policies were.

None of steps 1-7 have been implemented. This report and the corrected
Phase Two implementation are the prerequisite, not the Phase Three work
itself.
