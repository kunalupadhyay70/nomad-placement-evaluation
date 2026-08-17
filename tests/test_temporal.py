import math

import pytest

from sim.models import Job, NodeState
from sim.policies import binpack, spread
from sim.temporal import (
    QUEUE_DISCIPLINE,
    TemporalJob,
    TemporalTrace,
    percentile_nearest_rank,
    run_temporal,
)


def _node(node_id="n0", cpu=10.0, ram=10.0, used_cpu=0.0, used_ram=0.0):
    return NodeState(node_id, cpu, ram, used_cpu, used_ram)


def _job(job_id, cpu, ram, arrival, duration, order):
    return TemporalJob(
        Job(job_id, cpu, ram, shape="manual", size_class="manual"),
        arrival_time=arrival,
        duration=duration,
        arrival_order=order,
    )


def _trace(nodes, jobs, horizon):
    return TemporalTrace(tuple(nodes), tuple(jobs), observation_horizon=horizon)


def _timeline(result, job_id):
    return next(item for item in result.ledger if item.job.job_id == job_id)


def test_one_node_hand_worked_lifecycle_and_release():
    trace = _trace([_node()], [_job("j1", 6, 6, 0, 5, 0)], horizon=10)

    result = run_temporal(trace, binpack, "binpack", drain=True)
    item = result.ledger[0]

    assert item.start_time == 0
    assert item.completion_time == 5
    assert item.waiting_time == 0
    assert item.turnaround_time == 5
    assert item.slowdown == 1
    assert item.state_at_horizon == "completed"
    assert item.final_state == "completed"
    assert result.metrics.completed_by_horizon == 1
    assert result.metrics.throughput_horizon == pytest.approx(0.1)
    assert result.metrics.time_weighted_cpu_utilization == pytest.approx(0.3)
    assert result.metrics.time_weighted_ram_utilization == pytest.approx(0.3)
    assert result.metrics.time_weighted_active_nodes == pytest.approx(0.5)
    assert result.final_cluster == trace.cluster


def test_waiting_metrics_and_time_weighted_queue_are_hand_checkable():
    trace = _trace(
        [_node()],
        [
            _job("j1", 6, 6, 0, 5, 0),
            _job("j2", 6, 6, 1, 2, 1),
        ],
        horizon=6,
    )

    result = run_temporal(trace, binpack, "binpack", drain=True)
    metrics = result.metrics

    assert _timeline(result, "j2").start_time == 5
    assert metrics.completed_by_horizon == 1
    assert metrics.running_at_horizon == 1
    assert metrics.queued_at_horizon == 0
    assert metrics.throughput_horizon == pytest.approx(1 / 6)
    assert metrics.time_weighted_cpu_utilization == pytest.approx(0.6)
    assert metrics.time_weighted_queue_length == pytest.approx(4 / 6)
    assert metrics.time_weighted_active_nodes == pytest.approx(1.0)
    assert metrics.mean_wait_time == pytest.approx(2.0)
    assert metrics.p95_wait_time == pytest.approx(4.0)
    assert metrics.mean_turnaround_time == pytest.approx(5.5)
    assert metrics.p95_turnaround_time == pytest.approx(6.0)
    assert metrics.mean_slowdown == pytest.approx(2.0)
    assert metrics.p95_slowdown == pytest.approx(3.0)
    assert metrics.makespan == pytest.approx(7.0)
    assert metrics.drained_throughput == pytest.approx(2 / 7)


def test_job_waits_until_completion_releases_resources():
    trace = _trace(
        [_node()],
        [_job("first", 10, 10, 0, 4, 0), _job("waiting", 10, 10, 1, 2, 1)],
        horizon=5,
    )
    result = run_temporal(trace, binpack, "binpack")

    waiting = _timeline(result, "waiting")
    assert waiting.start_time == 4
    assert waiting.waiting_time == 3
    assert waiting.node_id == "n0"


def test_multiple_simultaneous_completions_release_before_dispatch():
    trace = _trace(
        [_node("n0"), _node("n1")],
        [
            _job("a", 10, 10, 0, 5, 0),
            _job("b", 10, 10, 0, 5, 1),
            _job("c", 10, 10, 1, 2, 2),
            _job("d", 10, 10, 1, 2, 3),
        ],
        horizon=6,
    )
    result = run_temporal(trace, binpack, "binpack")

    assert _timeline(result, "c").start_time == 5
    assert _timeline(result, "d").start_time == 5
    assert result.final_cluster == trace.cluster


def test_completion_and_arrival_at_same_time_allows_immediate_start():
    trace = _trace(
        [_node()],
        [_job("finishing", 10, 10, 0, 5, 0), _job("arriving", 10, 10, 5, 2, 1)],
        horizon=6,
    )
    result = run_temporal(trace, binpack, "binpack")

    arriving = _timeline(result, "arriving")
    assert arriving.start_time == 5
    assert arriving.waiting_time == 0


def test_simultaneous_arrivals_use_stable_arrival_order_then_job_id():
    trace = _trace(
        [_node()],
        [
            _job("later-order", 10, 10, 0, 2, 5),
            _job("first-order", 10, 10, 0, 2, 1),
        ],
        horizon=1,
    )
    result = run_temporal(trace, binpack, "binpack")

    assert _timeline(result, "first-order").start_time == 0
    assert _timeline(result, "later-order").start_time == 2


def test_fifo_scan_backfills_around_blocked_job():
    trace = _trace(
        [_node()],
        [
            _job("running", 4, 4, 0, 10, 0),
            _job("blocked-head", 8, 8, 1, 2, 1),
            _job("backfill", 6, 6, 1, 2, 2),
        ],
        horizon=5,
    )
    result = run_temporal(trace, binpack, "binpack")

    assert result.queue_discipline == QUEUE_DISCIPLINE
    assert _timeline(result, "backfill").start_time == 1
    assert _timeline(result, "blocked-head").start_time == 10


def test_permanently_infeasible_job_never_enters_queue():
    trace = _trace([_node()], [_job("too-big", 11, 1, 0, 2, 0)], horizon=5)
    result = run_temporal(trace, binpack, "binpack")

    item = result.ledger[0]
    assert item.state_at_horizon == "permanently_infeasible"
    assert item.final_state == "permanently_infeasible"
    assert item.start_time is None
    assert result.metrics.permanently_infeasible_jobs == 1
    assert result.metrics.feasible_jobs == 0
    assert result.metrics.admission_ratio_horizon is None
    assert result.metrics.completion_ratio_horizon is None
    assert result.metrics.time_weighted_queue_length == 0


def test_heterogeneous_nodes_release_to_original_node():
    trace = _trace(
        [_node("small", 10, 10), _node("large", 20, 20)],
        [_job("j", 5, 5, 0, 3, 0)],
        horizon=4,
    )
    result = run_temporal(trace, spread, "spread")

    assert result.ledger[0].node_id == "large"
    assert result.final_cluster == trace.cluster


def test_horizon_snapshot_without_drain_keeps_running_allocation():
    trace = _trace([_node()], [_job("j", 10, 10, 0, 10, 0)], horizon=5)
    result = run_temporal(trace, binpack, "binpack", drain=False)

    item = result.ledger[0]
    assert item.state_at_horizon == "running_at_horizon"
    assert item.final_state == "unfinished_running"
    assert item.completion_time is None
    assert result.metrics.running_at_horizon == 1
    assert result.metrics.drained is False
    assert result.metrics.makespan is None
    assert result.metrics.drained_throughput is None
    assert result.final_cluster[0].used_cpu == 10


def test_drain_preserves_horizon_state_and_completes_job():
    trace = _trace([_node()], [_job("j", 10, 10, 0, 10, 0)], horizon=5)
    result = run_temporal(trace, binpack, "binpack", drain=True)

    item = result.ledger[0]
    assert item.state_at_horizon == "running_at_horizon"
    assert item.final_state == "completed"
    assert item.completion_time == 10
    assert result.final_cluster == trace.cluster


def test_queued_at_horizon_is_reported_not_treated_as_completed():
    trace = _trace(
        [_node()],
        [_job("running", 10, 10, 0, 10, 0), _job("queued", 10, 10, 1, 1, 1)],
        horizon=5,
    )
    result = run_temporal(trace, binpack, "binpack", drain=True)

    queued = _timeline(result, "queued")
    assert queued.state_at_horizon == "queued_at_horizon"
    assert queued.final_state == "completed"
    assert result.metrics.queued_at_horizon == 1
    assert result.metrics.completed_by_horizon == 0


@pytest.mark.parametrize("duration", [0, -1, math.nan, math.inf])
def test_duration_must_be_positive_and_finite(duration):
    with pytest.raises(ValueError):
        _job("bad", 1, 1, 0, duration, 0)


def test_arrival_time_and_order_validation():
    with pytest.raises(ValueError):
        _job("bad-time", 1, 1, -1, 1, 0)
    with pytest.raises(ValueError):
        _job("bad-order", 1, 1, 0, 1, -1)


def test_trace_rejects_duplicate_ids_and_arrivals_after_horizon():
    duplicate = _job("same", 1, 1, 0, 1, 0)
    with pytest.raises(ValueError):
        _trace([_node()], [duplicate, duplicate], horizon=2)
    with pytest.raises(ValueError):
        _trace([_node()], [_job("late", 1, 1, 3, 1, 0)], horizon=2)


def test_zero_jobs_has_defined_rates_and_undefined_samples():
    result = run_temporal(_trace([_node()], [], horizon=5), binpack, "binpack")
    metrics = result.metrics

    assert metrics.total_jobs == 0
    assert metrics.throughput_horizon == 0
    assert metrics.admission_ratio_horizon is None
    assert metrics.completion_ratio_horizon is None
    assert metrics.mean_wait_time is None
    assert metrics.p95_wait_time is None
    assert metrics.makespan is None
    assert metrics.drained_throughput is None


def test_nearest_rank_percentile_and_empty_samples():
    assert percentile_nearest_rank([], 0.95) is None
    assert percentile_nearest_rank([1, 2, 3, 4], 0.50) == 2
    assert percentile_nearest_rank([1, 2, 3, 4], 0.95) == 4
    with pytest.raises(ValueError):
        percentile_nearest_rank([1], 0)


def test_temporal_run_is_deterministic_and_does_not_mutate_trace():
    trace = _trace(
        [_node("n0"), _node("n1")],
        [_job("a", 7, 2, 0, 3, 0), _job("b", 2, 7, 1, 4, 1)],
        horizon=6,
    )
    before = trace

    first = run_temporal(trace, binpack, "binpack")
    second = run_temporal(trace, binpack, "binpack")

    assert first == second
    assert trace == before


def test_unknown_queue_discipline_is_rejected():
    trace = _trace([_node()], [], horizon=1)
    with pytest.raises(ValueError):
        run_temporal(trace, binpack, "binpack", queue_discipline="strict_fifo")
