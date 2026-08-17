"""Seeded temporal traces built from the existing resource-shape families.

Arrivals follow a Poisson process (exponential inter-arrival times). Durations
are independent bounded uniforms, avoiding heavy-tail domination in a compact
simulation. The arrival rate is derived from resource-time demand:

    rho_r = lambda * E[request_r * duration] / cluster_capacity_r
    rho   = max(rho_cpu, rho_ram)

The three frozen target regions are underload (0.70), near saturation (1.00),
and overload (1.30). Calibration uses Phase-3 tuning seeds; held-out seeds stay
1000--1009. Independent derived RNG streams generate the cluster, arrivals,
resource-class order, and durations, so policy execution cannot consume trace
randomness.
"""

from __future__ import annotations

import math
import random
from typing import Mapping

from sim.families import (
    MIX_BALANCED,
    MIX_BIMODAL,
    MIX_CPU,
    MIX_CPU_BIASED,
    MIX_RAM,
    MIX_TINY_LARGE,
    _adversarial_order,
    _counts,
    flip_job,
    make_cluster,
)
from sim.models import Job, NodeState
from sim.temporal import TemporalJob, TemporalTrace
from sim.workload import generate_workload, job_resources

TEMPORAL_LOADS: dict[str, float] = {
    "low": 0.70,
    "med": 1.00,
    "high": 1.30,
}
OBSERVATION_HORIZON = 50.0
DURATION_RANGE = (8.0, 12.0)

_STREAM_MULTIPLIER = 100_000
_CLUSTER_STREAM = 101
_ARRIVAL_STREAM = 211
_WORKLOAD_STREAM = 307
_DURATION_STREAM = 401
_DRIFT_SECOND_STREAM = 503


def _average_mixes(
    first: Mapping[tuple[str, str], float],
    second: Mapping[tuple[str, str], float],
) -> dict[tuple[str, str], float]:
    keys = set(first) | set(second)
    return {key: 0.5 * first.get(key, 0.0) + 0.5 * second.get(key, 0.0) for key in keys}


def family_mix(family: str) -> Mapping[tuple[str, str], float]:
    """Return the theoretical class mix used to calibrate temporal load."""

    mixes = {
        "balanced": MIX_BALANCED,
        "cpu_heavy": MIX_CPU,
        "ram_heavy": MIX_RAM,
        "bimodal": MIX_BIMODAL,
        "tiny_large": MIX_TINY_LARGE,
        "drift": _average_mixes(MIX_CPU, MIX_RAM),
        "pred_error": MIX_CPU_BIASED,
        "adversarial": MIX_BIMODAL,
    }
    try:
        return mixes[family]
    except KeyError as exc:
        raise ValueError(f"unknown temporal workload family {family!r}") from exc


def mean_resource_demand(mix: Mapping[tuple[str, str], float]) -> tuple[float, float]:
    weight_sum = sum(mix.values())
    if weight_sum <= 0:
        raise ValueError("class mix must have positive total weight")
    mean_cpu = sum(
        weight * job_resources(size, shape)[0]
        for (size, shape), weight in mix.items()
    ) / weight_sum
    mean_ram = sum(
        weight * job_resources(size, shape)[1]
        for (size, shape), weight in mix.items()
    ) / weight_sum
    return mean_cpu, mean_ram


def arrival_rate_for_target(
    cluster: tuple[NodeState, ...],
    mix: Mapping[tuple[str, str], float],
    target_rho: float,
    mean_duration: float,
) -> float:
    """Calculate Poisson arrival rate for the target max resource-time load."""

    if not math.isfinite(target_rho) or target_rho <= 0:
        raise ValueError("target_rho must be finite and > 0")
    if not math.isfinite(mean_duration) or mean_duration <= 0:
        raise ValueError("mean_duration must be finite and > 0")
    capacity_cpu = sum(node.total_cpu - node.used_cpu for node in cluster)
    capacity_ram = sum(node.total_ram - node.used_ram for node in cluster)
    mean_cpu, mean_ram = mean_resource_demand(mix)
    resource_time_per_arrival = max(
        mean_cpu * mean_duration / capacity_cpu,
        mean_ram * mean_duration / capacity_ram,
    )
    return target_rho / resource_time_per_arrival


def _family_jobs(family: str, count: int, seed: int) -> tuple[Job, ...]:
    if count < 0:
        raise ValueError("count must be >= 0")
    if count == 0:
        return ()

    if family == "drift":
        first_count = count // 2
        first = generate_workload(
            _counts(MIX_CPU, first_count),
            seed=seed,
            shuffle=True,
        )
        second = generate_workload(
            _counts(MIX_RAM, count - first_count),
            seed=seed + _DRIFT_SECOND_STREAM,
            shuffle=True,
        )
        ordered = first + second
    else:
        mix = family_mix(family)
        ordered = generate_workload(
            _counts(mix, count),
            seed=seed,
            shuffle=family != "adversarial",
        )
        if family == "adversarial":
            ordered = _adversarial_order(ordered)

    # IDs are trace-global and reflect the final deterministic arrival order.
    return tuple(
        Job(
            job_id=f"temporal-{index:06d}-{job.job_id}",
            cpu=job.cpu,
            ram=job.ram,
            shape=job.shape,
            size_class=job.size_class,
        )
        for index, job in enumerate(ordered)
    )


def generate_temporal_trace(
    family: str,
    cluster_config: str,
    load: str,
    seed: int,
    *,
    observation_horizon: float = OBSERVATION_HORIZON,
    duration_range: tuple[float, float] = DURATION_RANGE,
    target_rho: float | None = None,
) -> TemporalTrace:
    """Build one deterministic temporal trace, independent of any policy."""

    if load not in TEMPORAL_LOADS:
        raise ValueError(f"unknown temporal load {load!r}; valid: {sorted(TEMPORAL_LOADS)}")
    if not math.isfinite(observation_horizon) or observation_horizon <= 0:
        raise ValueError("observation_horizon must be finite and > 0")
    duration_min, duration_max = duration_range
    if (
        not math.isfinite(duration_min)
        or not math.isfinite(duration_max)
        or duration_min <= 0
        or duration_max < duration_min
    ):
        raise ValueError("duration_range must be finite, positive, and ordered")

    target = TEMPORAL_LOADS[load] if target_rho is None else target_rho
    stream_base = seed * _STREAM_MULTIPLIER
    cluster = make_cluster(cluster_config, seed=stream_base + _CLUSTER_STREAM)
    mix = family_mix(family)
    mean_duration = (duration_min + duration_max) / 2.0
    arrival_rate = arrival_rate_for_target(cluster, mix, target, mean_duration)

    arrival_rng = random.Random(stream_base + _ARRIVAL_STREAM)
    arrival_times: list[float] = []
    current = 0.0
    while True:
        current += arrival_rng.expovariate(arrival_rate)
        if current > observation_horizon:
            break
        arrival_times.append(current)

    jobs = _family_jobs(family, len(arrival_times), stream_base + _WORKLOAD_STREAM)
    duration_rng = random.Random(stream_base + _DURATION_STREAM)
    temporal_jobs = []
    for index, (job, arrival_time) in enumerate(zip(jobs, arrival_times)):
        duration = duration_rng.uniform(duration_min, duration_max)
        observed = flip_job(job) if family == "pred_error" else job
        temporal_jobs.append(
            TemporalJob(
                job=job,
                arrival_time=arrival_time,
                duration=duration,
                arrival_order=index,
                observed_job=observed,
            )
        )

    capacity_cpu = sum(node.total_cpu - node.used_cpu for node in cluster)
    capacity_ram = sum(node.total_ram - node.used_ram for node in cluster)
    realized_cpu = (
        sum(item.job.cpu * item.duration for item in temporal_jobs)
        / (capacity_cpu * observation_horizon)
    )
    realized_ram = (
        sum(item.job.ram * item.duration for item in temporal_jobs)
        / (capacity_ram * observation_horizon)
    )

    return TemporalTrace(
        cluster=cluster,
        jobs=tuple(temporal_jobs),
        observation_horizon=observation_horizon,
        family=family,
        cluster_config=cluster_config,
        load=load,
        seed=seed,
        target_rho=target,
        realized_rho_cpu=realized_cpu,
        realized_rho_ram=realized_ram,
        arrival_rate=arrival_rate,
        duration_min=duration_min,
        duration_max=duration_max,
    )
