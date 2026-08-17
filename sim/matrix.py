"""Lean multi-seed experiment-matrix runner with seed discipline.

This module deliberately does NOT reuse sim.experiment.run_policy for
matrix runs: that runner computes full cluster metrics after every job
and greedy repeated-placement estimates at the end, which is fine for a
single smoke run but dominates runtime across thousands of matrix cells.
Here we track only scalar aggregates during the run and compute final
metrics once. sim.experiment remains the detailed single-run tool.

Seed discipline
---------------
    TUNE_SEEDS = (0..9)         tuning/training ONLY
    VAL_SEEDS  = (100..104)     variant selection sanity checks
    TEST_SEEDS = (1000..1009)   final held-out evaluation, run once

`PolicyConfig.tuned_on` records which split a config's tunables were
fitted on. `run_grid(..., split="test")` raises if any config with
tunable parameters is not marked as tuned on "tune" -- final CSVs cannot
be produced from weights fitted on validation or test seeds.

Metric definitions (admission-only; no durations/queue exist yet, so
"admission rate" is NOT throughput -- see DESIGN.md):

    admission_rate   placed / submitted
    mean_*_util      mean_n used_r(n) / total_r(n) over final state
    active_nodes     #{n : used_cpu > 0 or used_ram > 0} at end
    free_imbalance   mean_n |free_cpu_frac(n) - free_ram_frac(n)| at end
    stranded_frac    sum of (free_cpu + free_ram) over nodes that cannot
                     fit the SMALLEST representative job class present in
                     the trace, divided by total (cpu + ram) capacity
    latency          wall time of one full placement decision (feasibility
                     + scoring + state update + observe), perf_counter_ns;
                     mean and p95 reported per run, plus decisions/sec
"""

from __future__ import annotations

import csv
import statistics
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from sim.families import Trace, generate_trace
from sim.models import Job, NodeState
from sim.scheduler import place

TUNE_SEEDS: Tuple[int, ...] = tuple(range(10))
VAL_SEEDS: Tuple[int, ...] = tuple(range(100, 105))
TEST_SEEDS: Tuple[int, ...] = tuple(range(1000, 1010))
SPLITS: Dict[str, Tuple[int, ...]] = {
    "tune": TUNE_SEEDS,
    "val": VAL_SEEDS,
    "test": TEST_SEEDS,
}

PolicyFactory = Callable[[], Callable[[NodeState, Job], float]]


@dataclass(frozen=True)
class PolicyConfig:
    """A named policy configuration.

    `factory` must return a FRESH policy instance per run (stateful
    policies carry an EWMA profile). `tunable` marks configs whose
    parameters were selected from data; those must have tuned_on="tune"
    to be allowed in a test-split run. `params` is logged to CSV.
    """

    name: str
    factory: PolicyFactory
    tunable: bool = False
    tuned_on: Optional[str] = None
    params: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RunRecord:
    split: str
    family: str
    cluster_config: str
    load: str
    policy: str
    seed: int
    submitted: int
    placed: int
    rejected: int
    admission_rate: float
    mean_cpu_util: float
    mean_ram_util: float
    active_nodes: int
    free_imbalance: float
    stranded_frac: float
    decisions_per_sec: float
    mean_latency_us: float
    p95_latency_us: float
    params: str = ""


def _stranded_frac(cluster: Sequence[NodeState], smallest: Tuple[float, float]) -> float:
    cpu_d, ram_d = smallest
    stranded = 0.0
    capacity = 0.0
    for n in cluster:
        free_cpu = n.total_cpu - n.used_cpu
        free_ram = n.total_ram - n.used_ram
        capacity += n.total_cpu + n.total_ram
        if free_cpu < cpu_d or free_ram < ram_d:
            stranded += free_cpu + free_ram
    return stranded / capacity if capacity > 0 else 0.0


def run_trace(trace: Trace, policy: Callable[[NodeState, Job], float]) -> Dict[str, float]:
    """Run one policy over one trace; returns scalar aggregates only.

    The policy's `observe` hook (if present) is called with the trace's
    observation stream AFTER each placement decision (predict-then-update:
    job t is scored by a profile that has seen only jobs < t).
    """
    observe = getattr(policy, "observe", None)
    current = trace.cluster
    placed = 0
    latencies: List[int] = []

    for job, obs in zip(trace.jobs, trace.observed):
        t0 = time.perf_counter_ns()
        result, current = place(current, job, policy)
        if observe is not None:
            observe(obs)
        latencies.append(time.perf_counter_ns() - t0)
        if result.placed:
            placed += 1

    submitted = len(trace.jobs)
    cpu_utils = [n.used_cpu / n.total_cpu for n in current]
    ram_utils = [n.used_ram / n.total_ram for n in current]
    imbalance = [abs(n.free_cpu_fraction - n.free_ram_fraction) for n in current]
    # Smallest representative job class present in this trace (by cpu+ram).
    smallest_job = min(trace.jobs, key=lambda j: j.cpu + j.ram)
    total_ns = sum(latencies)
    lat_sorted = sorted(latencies)
    p95 = lat_sorted[min(len(lat_sorted) - 1, int(0.95 * len(lat_sorted)))]

    return dict(
        submitted=submitted,
        placed=placed,
        rejected=submitted - placed,
        admission_rate=placed / submitted,
        mean_cpu_util=statistics.fmean(cpu_utils),
        mean_ram_util=statistics.fmean(ram_utils),
        active_nodes=sum(1 for n in current if n.used_cpu > 0 or n.used_ram > 0),
        free_imbalance=statistics.fmean(imbalance),
        stranded_frac=_stranded_frac(current, (smallest_job.cpu, smallest_job.ram)),
        decisions_per_sec=submitted / (total_ns / 1e9) if total_ns else float("inf"),
        mean_latency_us=(total_ns / submitted) / 1e3,
        p95_latency_us=p95 / 1e3,
    )


def run_grid(
    configs: Sequence[PolicyConfig],
    cells: Sequence[Tuple[str, str, str]],  # (family, cluster_config, load)
    split: str,
    seeds: Optional[Sequence[int]] = None,
) -> List[RunRecord]:
    """Run every config on every cell x seed of `split`.

    Enforces seed discipline: on the test split, every tunable config
    must be marked tuned_on="tune". Traces are generated once per
    (cell, seed) and shared across configs, so every policy sees the
    byte-identical cluster, jobs, and arrival order.
    """
    if split not in SPLITS:
        raise ValueError(f"unknown split {split!r}; valid: {sorted(SPLITS)}")
    if split == "test":
        for cfg in configs:
            if cfg.tunable and cfg.tuned_on != "tune":
                raise ValueError(
                    f"config {cfg.name!r} is tunable but tuned_on={cfg.tuned_on!r}; "
                    "final evaluation requires tuned_on='tune'"
                )
    seeds = tuple(seeds) if seeds is not None else SPLITS[split]
    if not set(seeds) <= set(SPLITS[split]):
        raise ValueError(f"seeds {seeds!r} are not a subset of split {split!r}")

    records: List[RunRecord] = []
    for family, cluster_config, load in cells:
        for seed in seeds:
            trace = generate_trace(family, cluster_config, load, seed)
            for cfg in configs:
                stats = run_trace(trace, cfg.factory())
                records.append(
                    RunRecord(
                        split=split,
                        family=family,
                        cluster_config=cluster_config,
                        load=load,
                        policy=cfg.name,
                        seed=seed,
                        params=repr(cfg.params),
                        **stats,  # type: ignore[arg-type]
                    )
                )
    return records


def write_csv(records: Sequence[RunRecord], path: str) -> None:
    if not records:
        raise ValueError("no records to write")
    fields = list(RunRecord.__dataclass_fields__)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in records:
            w.writerow({k: getattr(r, k) for k in fields})


# --- Paired statistics (dependency-free) -----------------------------------

# Two-sided 95% critical values of Student's t for small df.
_T95 = {4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 14: 2.145, 29: 2.045}


def _t95(df: int) -> float:
    if df in _T95:
        return _T95[df]
    if df < 4:
        raise ValueError("need at least 5 paired seeds for a CI")
    return 1.96 if df > 29 else min(v for k, v in _T95.items() if k >= df)


@dataclass(frozen=True)
class PairedComparison:
    """Per-seed paired deltas (candidate - baseline) on one metric."""

    n: int
    mean_delta: float
    sd_delta: float
    ci95_low: float
    ci95_high: float
    wins: int
    losses: int
    ties: int


def paired_compare(deltas: Sequence[float]) -> PairedComparison:
    n = len(deltas)
    mean = statistics.fmean(deltas)
    sd = statistics.stdev(deltas) if n > 1 else 0.0
    half = _t95(n - 1) * sd / (n ** 0.5) if n > 1 else 0.0
    return PairedComparison(
        n=n,
        mean_delta=mean,
        sd_delta=sd,
        ci95_low=mean - half,
        ci95_high=mean + half,
        wins=sum(1 for d in deltas if d > 0),
        losses=sum(1 for d in deltas if d < 0),
        ties=sum(1 for d in deltas if d == 0),
    )


def paired_vs_baseline(
    records: Sequence[RunRecord],
    baseline: str,
    metric: str = "admission_rate",
) -> Dict[Tuple[str, str, str, str], PairedComparison]:
    """Group records by (family, cluster, load, policy) and compare each
    policy to `baseline` with per-seed pairing on `metric`."""
    by_key: Dict[Tuple[str, str, str, str, int], float] = {}
    for r in records:
        by_key[(r.family, r.cluster_config, r.load, r.policy, r.seed)] = getattr(r, metric)

    cells = sorted({(r.family, r.cluster_config, r.load) for r in records})
    policies = sorted({r.policy for r in records} - {baseline})
    seeds = sorted({r.seed for r in records})

    out: Dict[Tuple[str, str, str, str], PairedComparison] = {}
    for fam, cc, load in cells:
        for pol in policies:
            deltas = []
            for s in seeds:
                a = by_key.get((fam, cc, load, pol, s))
                b = by_key.get((fam, cc, load, baseline, s))
                if a is not None and b is not None:
                    deltas.append(a - b)
            if deltas:
                out[(fam, cc, load, pol)] = paired_compare(deltas)
    return out
