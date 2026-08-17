import pytest

import sim.temporal_matrix as temporal_matrix
from sim.experimental import WorkloadAwarePolicy
from sim.matrix import PolicyConfig
from sim.policies import binpack
from sim.temporal_matrix import (
    FROZEN_HYBRID_ALPHA,
    FROZEN_HYBRID_BETA,
    primary_temporal_configs,
    read_temporal_csv,
    run_temporal_grid,
    write_temporal_csv,
)


def test_primary_policy_set_is_frozen_to_four_phase3_choices():
    configs = primary_temporal_configs()
    assert [config.name for config in configs] == ["binpack", "spread", "tetris", "hybrid"]
    hybrid = configs[-1].factory()
    assert isinstance(hybrid, WorkloadAwarePolicy)
    assert (hybrid.alpha, hybrid.beta, hybrid.gamma) == (
        FROZEN_HYBRID_ALPHA,
        FROZEN_HYBRID_BETA,
        0.0,
    )


def test_temporal_grid_reuses_identical_trace_inputs_across_policies(monkeypatch):
    fingerprints = []
    original_run_temporal = temporal_matrix.run_temporal

    def capture_trace(trace, *args, **kwargs):
        fingerprints.append(
            tuple(
                (
                    item.job_id,
                    item.job.cpu,
                    item.job.ram,
                    item.arrival_time,
                    item.duration,
                    item.arrival_order,
                )
                for item in trace.jobs
            )
        )
        return original_run_temporal(trace, *args, **kwargs)

    monkeypatch.setattr(temporal_matrix, "run_temporal", capture_trace)
    records = run_temporal_grid(
        primary_temporal_configs(),
        [("bimodal", "homog", "low")],
        split="tune",
        seeds=[0],
    )

    assert len(records) == 4
    shared_fields = {
        (
            record.submitted,
            record.feasible_jobs,
            record.target_rho,
            record.realized_rho_cpu,
            record.realized_rho_ram,
            record.arrival_rate,
            record.observation_horizon,
            record.duration_min,
            record.duration_max,
        )
        for record in records
    }
    assert len(shared_fields) == 1
    assert len(fingerprints) == 4
    assert len(set(fingerprints)) == 1
    assert all(record.permanently_infeasible == 0 for record in records)
    assert all(record.makespan is not None for record in records)


def test_temporal_grid_is_deterministic():
    configs = [PolicyConfig("binpack", lambda: binpack)]
    args = (configs, [("balanced", "homog", "low")], "tune")
    first = run_temporal_grid(*args, seeds=[0])
    second = run_temporal_grid(*args, seeds=[0])
    assert first == second


def test_temporal_test_split_rejects_unfrozen_tunable_policy():
    bad = PolicyConfig(
        "bad",
        lambda: WorkloadAwarePolicy(0.1, 0.9, 0.0),
        tunable=True,
        tuned_on=None,
    )
    with pytest.raises(ValueError):
        run_temporal_grid([bad], [("balanced", "homog", "low")], split="test")


def test_temporal_grid_rejects_seed_from_wrong_split():
    with pytest.raises(ValueError):
        run_temporal_grid(
            [PolicyConfig("binpack", lambda: binpack)],
            [("balanced", "homog", "low")],
            split="tune",
            seeds=[1000],
        )


def test_temporal_csv_round_trip(tmp_path):
    records = run_temporal_grid(
        [PolicyConfig("binpack", lambda: binpack)],
        [("balanced", "homog", "low")],
        split="tune",
        seeds=[0],
    )
    path = tmp_path / "records.csv"
    write_temporal_csv(records, str(path))
    assert read_temporal_csv(str(path)) == records
