"""Experiment runner: compare policies on an identical workload.

The identical `cluster` and `jobs` sequence is replayed independently for
each policy, each starting from the caller-supplied cluster's *values*
(NodeState is immutable, and `run_schedule`/`place` never mutate their
inputs, so one policy's run cannot observe or affect another's -- see
tests/test_experiment.py for a direct check of this).

METHODOLOGY CORRECTION (see METHODOLOGY_CORRECTION_REPORT.md): the
previous version generated a random `n_jobs`-sized workload from a single
conflated size/shape axis inside `run_experiment` itself. This version
takes an already-generated `cluster` and `jobs` sequence -- workload
construction now happens once, explicitly, via
`sim.workload.generate_workload`, so the exact composition is visible and
controllable by the caller rather than hidden behind an (n_nodes, n_jobs)
pair.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Mapping, Sequence, Tuple

from sim.metrics import ClusterMetrics, compute_class_fit_counts, compute_metrics
from sim.models import Job, NodeState
from sim.policies import Policy
from sim.scheduler import PlacementResult, place
from sim.workload import CANONICAL_CLASSES, class_key, job_resources


@dataclass(frozen=True)
class PolicyRunResult:
    policy_name: str
    jobs_offered: int
    jobs_placed: int
    jobs_failed: int
    score_tie_count: int  # number of job decisions where >1 feasible node tied for the winning score
    final_metrics: ClusterMetrics
    utilization_trace: Tuple[Tuple[float, float], ...]  # (mean_cpu_util, mean_ram_util) after each job
    placed_by_size: Dict[str, int]
    rejected_by_size: Dict[str, int]
    placed_by_shape: Dict[str, int]
    rejected_by_shape: Dict[str, int]
    placed_by_size_and_shape: Dict[str, int]
    rejected_by_size_and_shape: Dict[str, int]
    nodes_fitting_class: Dict[str, int]  # final-cluster per-class fit counts (see sim.metrics.compute_class_fit_counts)
    additional_jobs_placeable: Dict[str, int]  # greedy repeated-placement estimate, see _count_additional_placeable
    decisions: Tuple[PlacementResult, ...]  # full per-job placement trace, in arrival order
    final_cluster: Tuple[NodeState, ...]


def _bump(d: Dict[str, int], key: str) -> None:
    d[key] = d.get(key, 0) + 1


def _count_additional_placeable(
    cluster: Sequence[NodeState],
    policy: Policy,
    class_resources: Mapping[str, Tuple[float, float]],
) -> Dict[str, int]:
    """For each canonical class, estimate how many *additional* identical
    jobs of that class could be placed into `cluster` as it currently
    stands, using the same greedy `policy`-based placement rule as the
    main run, applied repeatedly to a scratch copy of `cluster` until no
    feasible node remains.

    This is a deterministic, clearly-specified estimate -- it is not
    optimal bin-packing, and classes are evaluated independently (each
    starts fresh from the same `cluster`, so this does not model placing a
    *mix* of classes together).
    """
    from sim.models import Job as _Job  # local import to avoid polluting module namespace

    results: Dict[str, int] = {}
    for name, (cpu, ram) in class_resources.items():
        scratch = tuple(cluster)
        placed = 0
        while True:
            probe = _Job(job_id=f"probe-{name}-{placed}", cpu=cpu, ram=ram)
            result, scratch = place(scratch, probe, policy)
            if not result.placed:
                break
            placed += 1
        results[name] = placed
    return results


def run_policy(
    cluster: Tuple[NodeState, ...],
    jobs: Sequence[Job],
    policy: Policy,
    policy_name: str,
) -> PolicyRunResult:
    current = cluster
    trace: List[Tuple[float, float]] = []
    decisions: List[PlacementResult] = []
    placed = 0
    failed = 0
    tie_count = 0

    placed_by_size: Dict[str, int] = {}
    rejected_by_size: Dict[str, int] = {}
    placed_by_shape: Dict[str, int] = {}
    rejected_by_shape: Dict[str, int] = {}
    placed_by_size_and_shape: Dict[str, int] = {}
    rejected_by_size_and_shape: Dict[str, int] = {}

    for job in jobs:
        result, current = place(current, job, policy)
        decisions.append(result)
        combo_key = class_key(job.size_class, job.shape)
        if result.placed:
            placed += 1
            _bump(placed_by_size, job.size_class)
            _bump(placed_by_shape, job.shape)
            _bump(placed_by_size_and_shape, combo_key)
        else:
            failed += 1
            _bump(rejected_by_size, job.size_class)
            _bump(rejected_by_shape, job.shape)
            _bump(rejected_by_size_and_shape, combo_key)
        if result.was_tie:
            tie_count += 1
        m = compute_metrics(current)
        trace.append((m.mean_cpu_utilization, m.mean_ram_utilization))

    final_metrics = compute_metrics(current)

    class_resources = {
        class_key(size, shape): job_resources(size, shape) for size, shape in CANONICAL_CLASSES
    }
    nodes_fitting_class = compute_class_fit_counts(current, class_resources)
    additional_placeable = _count_additional_placeable(current, policy, class_resources)

    return PolicyRunResult(
        policy_name=policy_name,
        jobs_offered=len(jobs),
        jobs_placed=placed,
        jobs_failed=failed,
        score_tie_count=tie_count,
        final_metrics=final_metrics,
        utilization_trace=tuple(trace),
        placed_by_size=placed_by_size,
        rejected_by_size=rejected_by_size,
        placed_by_shape=placed_by_shape,
        rejected_by_shape=rejected_by_shape,
        placed_by_size_and_shape=placed_by_size_and_shape,
        rejected_by_size_and_shape=rejected_by_size_and_shape,
        nodes_fitting_class=nodes_fitting_class,
        additional_jobs_placeable=additional_placeable,
        decisions=tuple(decisions),
        final_cluster=current,
    )


def run_experiment(
    policies: Mapping[str, Policy],
    cluster: Sequence[NodeState],
    jobs: Sequence[Job],
) -> Dict[str, PolicyRunResult]:
    """Run every policy in `policies` against the identical `cluster` and
    `jobs` sequence and arrival order. Each policy starts from the same
    input `cluster` values; `place`/`run_schedule` never mutate their
    inputs, so no policy's run can affect another's.
    """
    cluster = tuple(cluster)
    jobs = tuple(jobs)
    results: Dict[str, PolicyRunResult] = {}
    for name, policy in policies.items():
        results[name] = run_policy(cluster, jobs, policy, name)
    return results


def result_to_dict(result: PolicyRunResult) -> dict:
    return asdict(result)
