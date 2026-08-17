"""Paired multi-seed runner for the Phase-4 temporal experiment."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Sequence

from sim.experimental import WorkloadAwarePolicy, tetris_alignment
from sim.matrix import PolicyConfig, SPLITS
from sim.policies import binpack, spread
from sim.temporal import run_temporal
from sim.temporal_workload import generate_temporal_trace

FROZEN_HYBRID_ALPHA = 0.1
FROZEN_HYBRID_BETA = 0.9


@dataclass(frozen=True, slots=True)
class TemporalRunRecord:
    split: str
    family: str
    cluster_config: str
    load: str
    policy: str
    seed: int
    target_rho: float
    realized_rho_cpu: float
    realized_rho_ram: float
    realized_rho: float
    arrival_rate: float
    observation_horizon: float
    duration_min: float
    duration_max: float
    submitted: int
    feasible_jobs: int
    permanently_infeasible: int
    started_by_horizon: int
    completed_by_horizon: int
    running_at_horizon: int
    queued_at_horizon: int
    admission_ratio_horizon: float | None
    completion_ratio_horizon: float | None
    throughput_horizon: float
    mean_wait_time: float | None
    p95_wait_time: float | None
    mean_turnaround_time: float | None
    p95_turnaround_time: float | None
    mean_slowdown: float | None
    p95_slowdown: float | None
    time_weighted_cpu_utilization: float
    time_weighted_ram_utilization: float
    time_weighted_queue_length: float
    time_weighted_active_nodes: float
    horizon_active_nodes: int
    horizon_cpu_utilization: float
    horizon_ram_utilization: float
    horizon_free_imbalance: float
    makespan: float | None
    drained_throughput: float | None
    params: str = ""


def primary_temporal_configs() -> tuple[PolicyConfig, ...]:
    """The four pre-frozen primary policies; no temporal retuning."""

    return (
        PolicyConfig("binpack", lambda: binpack),
        PolicyConfig("spread", lambda: spread),
        PolicyConfig("tetris", lambda: tetris_alignment),
        PolicyConfig(
            "hybrid",
            lambda: WorkloadAwarePolicy(
                FROZEN_HYBRID_ALPHA,
                FROZEN_HYBRID_BETA,
                0.0,
            ),
            tunable=True,
            tuned_on="tune",
            params={
                "alpha": FROZEN_HYBRID_ALPHA,
                "beta": FROZEN_HYBRID_BETA,
                "gamma": 0.0,
                "source": "phase3_tuning",
            },
        ),
    )


def run_temporal_grid(
    configs: Sequence[PolicyConfig],
    cells: Sequence[tuple[str, str, str]],
    split: str,
    *,
    seeds: Sequence[int] | None = None,
    check_invariants: bool = False,
) -> list[TemporalRunRecord]:
    """Run policies on shared immutable traces for every cell and seed."""

    if split not in SPLITS:
        raise ValueError(f"unknown split {split!r}; valid: {sorted(SPLITS)}")
    if split == "test":
        for config in configs:
            if config.tunable and config.tuned_on != "tune":
                raise ValueError(
                    f"config {config.name!r} is tunable but was not frozen on tune seeds"
                )
    selected_seeds = tuple(SPLITS[split] if seeds is None else seeds)
    if not set(selected_seeds) <= set(SPLITS[split]):
        raise ValueError(
            f"seeds {selected_seeds!r} are not a subset of split {split!r}"
        )

    records: list[TemporalRunRecord] = []
    for family, cluster_config, load in cells:
        for seed in selected_seeds:
            trace = generate_temporal_trace(family, cluster_config, load, seed)
            for config in configs:
                result = run_temporal(
                    trace,
                    config.factory(),
                    config.name,
                    drain=True,
                    check_invariants=check_invariants,
                )
                metrics = result.metrics
                assert trace.target_rho is not None
                assert trace.realized_rho_cpu is not None
                assert trace.realized_rho_ram is not None
                assert trace.arrival_rate is not None
                assert trace.duration_min is not None
                assert trace.duration_max is not None
                records.append(
                    TemporalRunRecord(
                        split=split,
                        family=family,
                        cluster_config=cluster_config,
                        load=load,
                        policy=config.name,
                        seed=seed,
                        target_rho=trace.target_rho,
                        realized_rho_cpu=trace.realized_rho_cpu,
                        realized_rho_ram=trace.realized_rho_ram,
                        realized_rho=max(trace.realized_rho_cpu, trace.realized_rho_ram),
                        arrival_rate=trace.arrival_rate,
                        observation_horizon=trace.observation_horizon,
                        duration_min=trace.duration_min,
                        duration_max=trace.duration_max,
                        submitted=metrics.total_jobs,
                        feasible_jobs=metrics.feasible_jobs,
                        permanently_infeasible=metrics.permanently_infeasible_jobs,
                        started_by_horizon=metrics.started_by_horizon,
                        completed_by_horizon=metrics.completed_by_horizon,
                        running_at_horizon=metrics.running_at_horizon,
                        queued_at_horizon=metrics.queued_at_horizon,
                        admission_ratio_horizon=metrics.admission_ratio_horizon,
                        completion_ratio_horizon=metrics.completion_ratio_horizon,
                        throughput_horizon=metrics.throughput_horizon,
                        mean_wait_time=metrics.mean_wait_time,
                        p95_wait_time=metrics.p95_wait_time,
                        mean_turnaround_time=metrics.mean_turnaround_time,
                        p95_turnaround_time=metrics.p95_turnaround_time,
                        mean_slowdown=metrics.mean_slowdown,
                        p95_slowdown=metrics.p95_slowdown,
                        time_weighted_cpu_utilization=metrics.time_weighted_cpu_utilization,
                        time_weighted_ram_utilization=metrics.time_weighted_ram_utilization,
                        time_weighted_queue_length=metrics.time_weighted_queue_length,
                        time_weighted_active_nodes=metrics.time_weighted_active_nodes,
                        horizon_active_nodes=metrics.horizon_active_nodes,
                        horizon_cpu_utilization=metrics.horizon_cpu_utilization,
                        horizon_ram_utilization=metrics.horizon_ram_utilization,
                        horizon_free_imbalance=metrics.horizon_free_imbalance,
                        makespan=metrics.makespan,
                        drained_throughput=metrics.drained_throughput,
                        params=repr(config.params),
                    )
                )
    return records

def write_temporal_csv(records: Sequence[TemporalRunRecord], path: str) -> None:
    if not records:
        raise ValueError("no temporal records to write")
    fieldnames = list(TemporalRunRecord.__dataclass_fields__)
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for record in records:
            writer.writerow({name: getattr(record, name) for name in fieldnames})


_STRING_FIELDS = {"split", "family", "cluster_config", "load", "policy", "params"}
_INT_FIELDS = {
    "seed",
    "submitted",
    "feasible_jobs",
    "permanently_infeasible",
    "started_by_horizon",
    "completed_by_horizon",
    "running_at_horizon",
    "queued_at_horizon",
    "horizon_active_nodes",
}


def read_temporal_csv(path: str) -> list[TemporalRunRecord]:
    records = []
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            parsed: dict[str, object] = {}
            for name, value in row.items():
                if name in _STRING_FIELDS:
                    parsed[name] = value
                elif name in _INT_FIELDS:
                    parsed[name] = int(value)
                else:
                    parsed[name] = None if value == "" else float(value)
            records.append(TemporalRunRecord(**parsed))  # type: ignore[arg-type]
    return records
