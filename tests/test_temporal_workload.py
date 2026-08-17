import pytest

from sim.temporal_workload import (
    DURATION_RANGE,
    OBSERVATION_HORIZON,
    TEMPORAL_LOADS,
    arrival_rate_for_target,
    family_mix,
    generate_temporal_trace,
    mean_resource_demand,
)


def test_temporal_trace_is_deterministic():
    first = generate_temporal_trace("bimodal", "hetero", "med", seed=7)
    second = generate_temporal_trace("bimodal", "hetero", "med", seed=7)
    assert first == second


def test_different_seed_changes_complete_trace():
    first = generate_temporal_trace("bimodal", "hetero", "med", seed=7)
    second = generate_temporal_trace("bimodal", "hetero", "med", seed=8)
    assert first != second


def test_arrivals_and_durations_obey_frozen_bounds():
    trace = generate_temporal_trace("balanced", "homog", "low", seed=0)
    arrivals = [job.arrival_time for job in trace.jobs]

    assert arrivals == sorted(arrivals)
    assert all(0 <= value <= OBSERVATION_HORIZON for value in arrivals)
    assert all(DURATION_RANGE[0] <= job.duration <= DURATION_RANGE[1] for job in trace.jobs)
    assert [job.arrival_order for job in trace.jobs] == list(range(len(trace.jobs)))


def test_arrival_rate_matches_theoretical_resource_time_target():
    trace = generate_temporal_trace("ram_heavy", "hetero", "high", seed=3)
    mix = family_mix("ram_heavy")
    mean_cpu, mean_ram = mean_resource_demand(mix)
    mean_duration = sum(DURATION_RANGE) / 2
    capacity_cpu = sum(node.total_cpu for node in trace.cluster)
    capacity_ram = sum(node.total_ram for node in trace.cluster)
    implied = max(
        trace.arrival_rate * mean_cpu * mean_duration / capacity_cpu,
        trace.arrival_rate * mean_ram * mean_duration / capacity_ram,
    )
    assert implied == pytest.approx(TEMPORAL_LOADS["high"])


def test_arrival_rate_helper_rejects_invalid_inputs():
    trace = generate_temporal_trace("balanced", "homog", "low", seed=0)
    mix = family_mix("balanced")
    with pytest.raises(ValueError):
        arrival_rate_for_target(trace.cluster, mix, 0, 10)
    with pytest.raises(ValueError):
        arrival_rate_for_target(trace.cluster, mix, 1, 0)


def test_realized_resource_time_load_reconciles_with_jobs():
    trace = generate_temporal_trace("tiny_large", "hetero", "med", seed=5)
    capacity_cpu = sum(node.total_cpu for node in trace.cluster)
    capacity_ram = sum(node.total_ram for node in trace.cluster)
    expected_cpu = sum(job.job.cpu * job.duration for job in trace.jobs) / (
        capacity_cpu * trace.observation_horizon
    )
    expected_ram = sum(job.job.ram * job.duration for job in trace.jobs) / (
        capacity_ram * trace.observation_horizon
    )
    assert trace.realized_rho_cpu == pytest.approx(expected_cpu)
    assert trace.realized_rho_ram == pytest.approx(expected_ram)


def test_higher_target_load_generates_more_arrivals_with_same_stream():
    low = generate_temporal_trace("balanced", "homog", "low", seed=4)
    med = generate_temporal_trace("balanced", "homog", "med", seed=4)
    high = generate_temporal_trace("balanced", "homog", "high", seed=4)
    assert len(low.jobs) < len(med.jobs) < len(high.jobs)


def test_drift_changes_shape_mix_between_trace_halves():
    trace = generate_temporal_trace("drift", "homog", "med", seed=2)
    midpoint = len(trace.jobs) // 2
    first_cpu_heavy = sum(job.job.shape == "cpu_heavy" for job in trace.jobs[:midpoint])
    second_cpu_heavy = sum(job.job.shape == "cpu_heavy" for job in trace.jobs[midpoint:])
    assert first_cpu_heavy > second_cpu_heavy


def test_adversarial_family_is_large_first():
    trace = generate_temporal_trace("adversarial", "homog", "med", seed=2)
    sizes = [job.job.size_class for job in trace.jobs]
    assert sizes.index("small") > sizes.index("large")


def test_prediction_error_observations_are_flipped():
    trace = generate_temporal_trace("pred_error", "homog", "low", seed=1)
    for item in trace.jobs:
        assert item.observed_job is not None
        assert item.observed_job.cpu == item.job.ram
        assert item.observed_job.ram == item.job.cpu


@pytest.mark.parametrize("family", ["balanced", "cpu_heavy", "ram_heavy", "bimodal"])
def test_temporal_job_ids_are_unique(family):
    trace = generate_temporal_trace(family, "hetero", "low", seed=1)
    ids = [job.job_id for job in trace.jobs]
    assert len(ids) == len(set(ids))


def test_invalid_family_load_horizon_and_duration_range_rejected():
    with pytest.raises(ValueError):
        generate_temporal_trace("unknown", "homog", "low", seed=0)
    with pytest.raises(ValueError):
        generate_temporal_trace("balanced", "homog", "unknown", seed=0)
    with pytest.raises(ValueError):
        generate_temporal_trace(
            "balanced", "homog", "low", seed=0, observation_horizon=0
        )
    with pytest.raises(ValueError):
        generate_temporal_trace(
            "balanced", "homog", "low", seed=0, duration_range=(2, 1)
        )
