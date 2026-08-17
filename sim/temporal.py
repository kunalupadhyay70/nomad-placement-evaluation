"""Deterministic event-driven job lifecycle simulation.

This module is additive: the Phase 1--3 sequential placement APIs remain
unchanged. Temporal jobs wrap the existing immutable :class:`sim.models.Job`,
and node selection still goes through :func:`sim.scheduler.place` and the same
policy score contract.

Event semantics at timestamp ``t`` are fixed:

1. release every job completing at ``t``;
2. register every job arriving at ``t`` in stable ``(arrival_order, job_id)``
   order, marking jobs that cannot fit on any baseline-empty node permanently
   infeasible;
3. scan the feasible queue in arrival order, starting every job that currently
   fits and leaving blocked jobs queued (FIFO-order scan with backfilling).

There is no preemption or migration. Time-weighted metrics integrate the
stepwise state over ``[0, observation_horizon)``. Optional drain mode stops new
arrivals at the horizon and continues completions/dispatch until every feasible
job finishes, providing uncensored wait and turnaround samples.
"""

from __future__ import annotations

import heapq
import math
import statistics
from dataclasses import dataclass, replace
from typing import Callable, Literal, Sequence

from sim.metrics import compute_metrics
from sim.models import Job, NodeState
from sim.policies import Policy
from sim.scheduler import place

QUEUE_DISCIPLINE = "fifo_scan_backfill"
_TOL = 1e-9

HorizonState = Literal[
    "completed",
    "permanently_infeasible",
    "running_at_horizon",
    "queued_at_horizon",
]
FinalState = Literal[
    "completed",
    "permanently_infeasible",
    "unfinished_running",
    "unfinished_queued",
]


@dataclass(frozen=True, slots=True)
class TemporalJob:
    """An immutable schedulable job with lifecycle timing."""

    job: Job
    arrival_time: float
    duration: float
    arrival_order: int
    observed_job: Job | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.arrival_time) or self.arrival_time < 0:
            raise ValueError(
                f"arrival_time must be finite and >= 0, got {self.arrival_time!r}"
            )
        if not math.isfinite(self.duration) or self.duration <= 0:
            raise ValueError(f"duration must be finite and > 0, got {self.duration!r}")
        if self.arrival_order < 0:
            raise ValueError(
                f"arrival_order must be >= 0, got {self.arrival_order!r}"
            )

    @property
    def job_id(self) -> str:
        return self.job.job_id


@dataclass(frozen=True, slots=True)
class TemporalTrace:
    """A complete immutable temporal input shared across policies."""

    cluster: tuple[NodeState, ...]
    jobs: tuple[TemporalJob, ...]
    observation_horizon: float
    family: str = "manual"
    cluster_config: str = "manual"
    load: str = "manual"
    seed: int = 0
    target_rho: float | None = None
    realized_rho_cpu: float | None = None
    realized_rho_ram: float | None = None
    arrival_rate: float | None = None
    duration_min: float | None = None
    duration_max: float | None = None

    def __post_init__(self) -> None:
        if not self.cluster:
            raise ValueError("cluster must contain at least one node")
        if not math.isfinite(self.observation_horizon) or self.observation_horizon <= 0:
            raise ValueError(
                "observation_horizon must be finite and > 0, "
                f"got {self.observation_horizon!r}"
            )
        node_ids = [node.node_id for node in self.cluster]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("cluster node IDs must be unique")
        job_ids = [job.job_id for job in self.jobs]
        if len(job_ids) != len(set(job_ids)):
            raise ValueError("temporal job IDs must be unique")
        too_late = [job.job_id for job in self.jobs if job.arrival_time > self.observation_horizon]
        if too_late:
            raise ValueError(
                "all temporal jobs must arrive by the observation horizon; "
                f"first late job: {too_late[0]!r}"
            )
        for name, value in (
            ("target_rho", self.target_rho),
            ("realized_rho_cpu", self.realized_rho_cpu),
            ("realized_rho_ram", self.realized_rho_ram),
            ("arrival_rate", self.arrival_rate),
            ("duration_min", self.duration_min),
            ("duration_max", self.duration_max),
        ):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and >= 0 when set")
        if (
            self.duration_min is not None
            and self.duration_max is not None
            and self.duration_min > self.duration_max
        ):
            raise ValueError("duration_min cannot exceed duration_max")


@dataclass(frozen=True, slots=True)
class JobTimeline:
    """Final ledger entry for one temporal job."""

    job: TemporalJob
    queue_entry_time: float
    start_time: float | None
    completion_time: float | None
    node_id: str | None
    state_at_horizon: HorizonState
    final_state: FinalState

    @property
    def waiting_time(self) -> float | None:
        if self.start_time is None:
            return None
        return self.start_time - self.job.arrival_time

    @property
    def turnaround_time(self) -> float | None:
        if self.completion_time is None:
            return None
        return self.completion_time - self.job.arrival_time

    @property
    def slowdown(self) -> float | None:
        turnaround = self.turnaround_time
        return None if turnaround is None else turnaround / self.job.duration


@dataclass(frozen=True, slots=True)
class TemporalMetrics:
    """Horizon and optional drain metrics for a temporal run.

    P95 values use the nearest-rank definition: after sorting ``n`` samples,
    select rank ``ceil(0.95 * n)`` (one-based). Empty samples return ``None``.
    """

    observation_horizon: float
    total_jobs: int
    feasible_jobs: int
    permanently_infeasible_jobs: int
    started_by_horizon: int
    completed_by_horizon: int
    running_at_horizon: int
    queued_at_horizon: int
    admission_ratio_horizon: float | None
    completion_ratio_horizon: float | None
    throughput_horizon: float
    time_weighted_cpu_utilization: float
    time_weighted_ram_utilization: float
    time_weighted_queue_length: float
    time_weighted_active_nodes: float
    horizon_active_nodes: int
    horizon_cpu_utilization: float
    horizon_ram_utilization: float
    horizon_free_imbalance: float
    wait_sample_count: int
    mean_wait_time: float | None
    p95_wait_time: float | None
    turnaround_sample_count: int
    mean_turnaround_time: float | None
    p95_turnaround_time: float | None
    mean_slowdown: float | None
    p95_slowdown: float | None
    drained: bool
    makespan: float | None
    drained_throughput: float | None


@dataclass(frozen=True, slots=True)
class TemporalRunResult:
    policy_name: str
    queue_discipline: str
    metrics: TemporalMetrics
    ledger: tuple[JobTimeline, ...]
    horizon_cluster: tuple[NodeState, ...]
    final_cluster: tuple[NodeState, ...]
    event_count: int


@dataclass(slots=True)
class _MutableTimeline:
    start_time: float | None = None
    completion_time: float | None = None
    node_id: str | None = None


@dataclass(frozen=True, slots=True)
class _Allocation:
    temporal_job: TemporalJob
    node_index: int
    completion_time: float
    start_order: int


def percentile_nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    """Return a deterministic nearest-rank percentile, or ``None`` if empty."""

    if not 0 < percentile <= 1:
        raise ValueError(f"percentile must be in (0, 1], got {percentile!r}")
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _fits_baseline_capacity(cluster: Sequence[NodeState], job: Job) -> bool:
    """Whether a job can fit after all temporal allocations have drained."""

    return any(
        job.cpu <= node.total_cpu - node.used_cpu
        and job.ram <= node.total_ram - node.used_ram
        for node in cluster
    )


def _release(node: NodeState, job: Job) -> NodeState:
    used_cpu = node.used_cpu - job.cpu
    used_ram = node.used_ram - job.ram
    if abs(used_cpu) <= _TOL:
        used_cpu = 0.0
    if abs(used_ram) <= _TOL:
        used_ram = 0.0
    if used_cpu < -_TOL or used_ram < -_TOL:
        raise AssertionError(f"resource release underflow on node {node.node_id!r}")
    return replace(node, used_cpu=max(0.0, used_cpu), used_ram=max(0.0, used_ram))


def run_temporal(
    trace: TemporalTrace,
    policy: Policy,
    policy_name: str,
    *,
    drain: bool = True,
    queue_discipline: str = QUEUE_DISCIPLINE,
    check_invariants: bool = True,
) -> TemporalRunResult:
    """Run one policy over an immutable temporal trace.

    ``fifo_scan_backfill`` scans queued jobs in stable arrival order at every
    dispatch point. A blocked earlier job remains queued, while a later job may
    start if it fits. Because starting work only consumes resources, one scan is
    sufficient at a timestamp.
    """

    if queue_discipline != QUEUE_DISCIPLINE:
        raise ValueError(
            f"unsupported queue discipline {queue_discipline!r}; "
            f"expected {QUEUE_DISCIPLINE!r}"
        )

    initial_cluster = tuple(trace.cluster)
    current = initial_cluster
    jobs = tuple(
        sorted(trace.jobs, key=lambda item: (item.arrival_time, item.arrival_order, item.job_id))
    )
    horizon = trace.observation_horizon
    timelines = {job.job_id: _MutableTimeline() for job in jobs}
    node_index = {node.node_id: index for index, node in enumerate(initial_cluster)}

    queue: list[TemporalJob] = []
    running: dict[str, _Allocation] = {}
    permanently_infeasible: set[str] = set()
    completion_heap: list[tuple[float, int, str]] = []
    arrival_index = 0
    start_counter = 0
    event_count = 0
    current_time = 0.0

    cpu_area = 0.0
    ram_area = 0.0
    queue_area = 0.0
    active_nodes_area = 0.0

    def assert_reconciled() -> None:
        if not check_invariants:
            return
        expected_cpu = [node.used_cpu for node in initial_cluster]
        expected_ram = [node.used_ram for node in initial_cluster]
        for allocation in running.values():
            expected_cpu[allocation.node_index] += allocation.temporal_job.job.cpu
            expected_ram[allocation.node_index] += allocation.temporal_job.job.ram
        for index, node in enumerate(current):
            if not math.isclose(node.used_cpu, expected_cpu[index], abs_tol=_TOL):
                raise AssertionError(
                    f"CPU allocation mismatch on {node.node_id}: "
                    f"state={node.used_cpu}, ledger={expected_cpu[index]}"
                )
            if not math.isclose(node.used_ram, expected_ram[index], abs_tol=_TOL):
                raise AssertionError(
                    f"RAM allocation mismatch on {node.node_id}: "
                    f"state={node.used_ram}, ledger={expected_ram[index]}"
                )
            if node.used_cpu < -_TOL or node.used_cpu > node.total_cpu + _TOL:
                raise AssertionError(f"CPU bounds violated on {node.node_id}")
            if node.used_ram < -_TOL or node.used_ram > node.total_ram + _TOL:
                raise AssertionError(f"RAM bounds violated on {node.node_id}")

    def advance(to_time: float) -> None:
        nonlocal current_time, cpu_area, ram_area, queue_area, active_nodes_area
        if to_time < current_time - _TOL:
            raise AssertionError("event time moved backwards")
        measured_end = min(to_time, horizon)
        measured_start = min(current_time, horizon)
        delta = measured_end - measured_start
        if delta > 0:
            total_cpu = sum(node.total_cpu for node in current)
            total_ram = sum(node.total_ram for node in current)
            cpu_area += (sum(node.used_cpu for node in current) / total_cpu) * delta
            ram_area += (sum(node.used_ram for node in current) / total_ram) * delta
            queue_area += len(queue) * delta
            active_nodes_area += sum(
                1 for node in current if node.used_cpu > 0 or node.used_ram > 0
            ) * delta
        current_time = to_time

    def process_completions(at_time: float) -> None:
        nonlocal current, event_count
        while completion_heap and completion_heap[0][0] == at_time:
            _, _, job_id = heapq.heappop(completion_heap)
            allocation = running.pop(job_id, None)
            if allocation is None:
                raise AssertionError(f"job {job_id!r} completed more than once")
            timeline = timelines[job_id]
            if timeline.start_time is None:
                raise AssertionError(f"job {job_id!r} completed before it started")
            if not math.isclose(
                at_time,
                timeline.start_time + allocation.temporal_job.duration,
                abs_tol=_TOL,
            ):
                raise AssertionError(f"completion time mismatch for job {job_id!r}")
            if timeline.completion_time is not None:
                raise AssertionError(f"job {job_id!r} completed more than once")
            timeline.completion_time = at_time
            index = allocation.node_index
            released = _release(current[index], allocation.temporal_job.job)
            current = tuple(released if i == index else node for i, node in enumerate(current))
            event_count += 1

    def dispatch(at_time: float) -> None:
        nonlocal current, queue, start_counter
        remaining: list[TemporalJob] = []
        for temporal_job in queue:
            placement, candidate_cluster = place(current, temporal_job.job, policy)
            if not placement.placed:
                remaining.append(temporal_job)
                continue
            timeline = timelines[temporal_job.job_id]
            if timeline.start_time is not None:
                raise AssertionError(f"job {temporal_job.job_id!r} started more than once")
            assert placement.node_id is not None
            index = node_index[placement.node_id]
            completion_time = at_time + temporal_job.duration
            timeline.start_time = at_time
            timeline.node_id = placement.node_id
            allocation = _Allocation(
                temporal_job=temporal_job,
                node_index=index,
                completion_time=completion_time,
                start_order=start_counter,
            )
            running[temporal_job.job_id] = allocation
            heapq.heappush(
                completion_heap,
                (completion_time, start_counter, temporal_job.job_id),
            )
            start_counter += 1
            current = candidate_cluster
        queue = remaining

    observe: Callable[[Job], None] | None = getattr(policy, "observe", None)

    # Phase A: process all events through the observation horizon.
    while True:
        next_arrival = jobs[arrival_index].arrival_time if arrival_index < len(jobs) else math.inf
        next_completion = completion_heap[0][0] if completion_heap else math.inf
        event_time = min(next_arrival, next_completion, horizon)
        advance(event_time)

        # Completion first at equal timestamps.
        process_completions(event_time)

        arrivals_now: list[TemporalJob] = []
        while arrival_index < len(jobs) and jobs[arrival_index].arrival_time == event_time:
            temporal_job = jobs[arrival_index]
            arrivals_now.append(temporal_job)
            if _fits_baseline_capacity(initial_cluster, temporal_job.job):
                queue.append(temporal_job)
            else:
                permanently_infeasible.add(temporal_job.job_id)
            arrival_index += 1
            event_count += 1

        dispatch(event_time)
        if observe is not None:
            for temporal_job in arrivals_now:
                observe(temporal_job.observed_job or temporal_job.job)
        assert_reconciled()

        if event_time == horizon:
            break

    horizon_cluster = current
    horizon_completed = {
        job_id
        for job_id, timeline in timelines.items()
        if timeline.completion_time is not None and timeline.completion_time <= horizon
    }
    horizon_running = set(running)
    horizon_queued = {job.job_id for job in queue}

    if set(timelines) != (
        horizon_completed | horizon_running | horizon_queued | permanently_infeasible
    ):
        raise AssertionError("horizon job states do not reconcile")

    # Phase B: stop arrivals and drain all feasible work if requested.
    if drain:
        while running or queue:
            if not completion_heap:
                raise AssertionError(
                    "feasible queued jobs cannot progress on the baseline cluster"
                )
            event_time = completion_heap[0][0]
            advance(event_time)
            process_completions(event_time)
            dispatch(event_time)
            assert_reconciled()

    if drain and running:
        raise AssertionError("running allocations remain after drain")
    if drain and queue:
        raise AssertionError("feasible queue remains after drain")
    if drain:
        for before, after in zip(initial_cluster, current):
            if not math.isclose(before.used_cpu, after.used_cpu, abs_tol=_TOL):
                raise AssertionError("CPU usage did not return to baseline after drain")
            if not math.isclose(before.used_ram, after.used_ram, abs_tol=_TOL):
                raise AssertionError("RAM usage did not return to baseline after drain")

    ledger: list[JobTimeline] = []
    for temporal_job in sorted(
        jobs, key=lambda item: (item.arrival_order, item.job_id)
    ):
        job_id = temporal_job.job_id
        timeline = timelines[job_id]
        if job_id in permanently_infeasible:
            horizon_state: HorizonState = "permanently_infeasible"
            final_state: FinalState = "permanently_infeasible"
        elif job_id in horizon_completed:
            horizon_state = "completed"
            final_state = "completed"
        elif job_id in horizon_running:
            horizon_state = "running_at_horizon"
            final_state = "completed" if timeline.completion_time is not None else "unfinished_running"
        elif job_id in horizon_queued:
            horizon_state = "queued_at_horizon"
            final_state = "completed" if timeline.completion_time is not None else "unfinished_queued"
        else:  # pragma: no cover - guarded by the reconciliation assertion above
            raise AssertionError(f"unknown horizon state for job {job_id!r}")
        ledger.append(
            JobTimeline(
                job=temporal_job,
                queue_entry_time=temporal_job.arrival_time,
                start_time=timeline.start_time,
                completion_time=timeline.completion_time,
                node_id=timeline.node_id,
                state_at_horizon=horizon_state,
                final_state=final_state,
            )
        )

    feasible_jobs = len(jobs) - len(permanently_infeasible)
    started_by_horizon = sum(
        1
        for item in ledger
        if item.start_time is not None and item.start_time <= horizon
    )
    completed_by_horizon = len(horizon_completed)
    wait_samples = [item.waiting_time for item in ledger if item.waiting_time is not None]
    turnaround_samples = [
        item.turnaround_time for item in ledger if item.turnaround_time is not None
    ]
    slowdown_samples = [item.slowdown for item in ledger if item.slowdown is not None]
    waits = [float(value) for value in wait_samples]
    turnarounds = [float(value) for value in turnaround_samples]
    slowdowns = [float(value) for value in slowdown_samples]

    horizon_metrics = compute_metrics(horizon_cluster)
    free_imbalances = [
        abs(node.free_cpu_fraction - node.free_ram_fraction)
        for node in horizon_cluster
    ]
    completion_times = [
        item.completion_time for item in ledger if item.completion_time is not None
    ]
    makespan = max(completion_times) if drain and completion_times else None
    drained_throughput = (
        feasible_jobs / makespan
        if drain and makespan is not None and makespan > 0
        else None
    )

    metrics = TemporalMetrics(
        observation_horizon=horizon,
        total_jobs=len(jobs),
        feasible_jobs=feasible_jobs,
        permanently_infeasible_jobs=len(permanently_infeasible),
        started_by_horizon=started_by_horizon,
        completed_by_horizon=completed_by_horizon,
        running_at_horizon=len(horizon_running),
        queued_at_horizon=len(horizon_queued),
        admission_ratio_horizon=(
            started_by_horizon / feasible_jobs if feasible_jobs else None
        ),
        completion_ratio_horizon=(
            completed_by_horizon / feasible_jobs if feasible_jobs else None
        ),
        throughput_horizon=completed_by_horizon / horizon,
        time_weighted_cpu_utilization=cpu_area / horizon,
        time_weighted_ram_utilization=ram_area / horizon,
        time_weighted_queue_length=queue_area / horizon,
        time_weighted_active_nodes=active_nodes_area / horizon,
        horizon_active_nodes=horizon_metrics.active_nodes,
        horizon_cpu_utilization=horizon_metrics.mean_cpu_utilization,
        horizon_ram_utilization=horizon_metrics.mean_ram_utilization,
        horizon_free_imbalance=statistics.fmean(free_imbalances),
        wait_sample_count=len(waits),
        mean_wait_time=_mean(waits),
        p95_wait_time=percentile_nearest_rank(waits, 0.95),
        turnaround_sample_count=len(turnarounds),
        mean_turnaround_time=_mean(turnarounds),
        p95_turnaround_time=percentile_nearest_rank(turnarounds, 0.95),
        mean_slowdown=_mean(slowdowns),
        p95_slowdown=percentile_nearest_rank(slowdowns, 0.95),
        drained=drain,
        makespan=makespan,
        drained_throughput=drained_throughput,
    )

    return TemporalRunResult(
        policy_name=policy_name,
        queue_discipline=queue_discipline,
        metrics=metrics,
        ledger=tuple(ledger),
        horizon_cluster=horizon_cluster,
        final_cluster=current,
        event_count=event_count,
    )
