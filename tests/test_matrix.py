import pytest

from sim.experimental import WorkloadAwarePolicy
from sim.families import FAMILIES, generate_trace, make_cluster
from sim.matrix import (
    PolicyConfig,
    SPLITS,
    paired_compare,
    paired_vs_baseline,
    run_grid,
    run_trace,
)
from sim.policies import binpack, spread


# --- Trace generation ------------------------------------------------------


def test_traces_are_deterministic():
    a = generate_trace("bimodal", "hetero", "med", seed=3)
    b = generate_trace("bimodal", "hetero", "med", seed=3)
    assert a.jobs == b.jobs
    assert a.cluster == b.cluster
    assert a.observed == b.observed


def test_different_seeds_differ():
    a = generate_trace("bimodal", "hetero", "med", seed=3)
    b = generate_trace("bimodal", "hetero", "med", seed=4)
    assert a.jobs != b.jobs


def test_homogeneous_cluster_is_homogeneous():
    cluster = make_cluster("homog", seed=1)
    assert len({(n.total_cpu, n.total_ram) for n in cluster}) == 1


def test_all_families_generate():
    for fam in FAMILIES:
        t = generate_trace(fam, "homog", "low", seed=0)
        assert len(t.jobs) > 0
        assert len(t.observed) == len(t.jobs)


def test_high_load_exceeds_capacity():
    t = generate_trace("balanced", "homog", "high", seed=0)
    total_cpu = sum(j.cpu for j in t.jobs)
    cap_cpu = sum(n.total_cpu for n in t.cluster)
    total_ram = sum(j.ram for j in t.jobs)
    cap_ram = sum(n.total_ram for n in t.cluster)
    assert max(total_cpu / cap_cpu, total_ram / cap_ram) > 1.0


def test_drift_trace_changes_composition_between_halves():
    t = generate_trace("drift", "homog", "med", seed=0)
    half = len(t.jobs) // 2
    first_cpu_heavy = sum(1 for j in t.jobs[:half] if j.shape == "cpu_heavy")
    second_cpu_heavy = sum(1 for j in t.jobs[half:] if j.shape == "cpu_heavy")
    assert first_cpu_heavy > second_cpu_heavy


def test_pred_error_observations_are_flipped():
    t = generate_trace("pred_error", "homog", "med", seed=0)
    for job, obs in zip(t.jobs, t.observed):
        assert obs.cpu == job.ram and obs.ram == job.cpu


def test_adversarial_order_is_deterministic_and_large_first():
    t = generate_trace("adversarial", "homog", "med", seed=5)
    t2 = generate_trace("adversarial", "homog", "med", seed=5)
    assert t.jobs == t2.jobs
    sizes = [j.size_class for j in t.jobs]
    assert sizes.index("small") > sizes.index("large")


# --- Runner ----------------------------------------------------------------


def test_run_trace_counts_reconcile():
    t = generate_trace("bimodal", "hetero", "med", seed=0)
    stats = run_trace(t, binpack)
    assert stats["submitted"] == len(t.jobs)
    assert stats["placed"] + stats["rejected"] == stats["submitted"]
    assert 0.0 <= stats["admission_rate"] <= 1.0
    assert stats["mean_latency_us"] > 0


def test_identical_traces_across_policies():
    """Every policy in a grid run must see the byte-identical trace."""
    t1 = generate_trace("bimodal", "hetero", "med", seed=0)
    # run two different policies; the trace object is shared by run_grid,
    # and here we verify a fresh generation is identical anyway.
    run_trace(t1, binpack)
    t2 = generate_trace("bimodal", "hetero", "med", seed=0)
    assert t1.jobs == t2.jobs and t1.cluster == t2.cluster


def test_run_trace_does_not_mutate_trace_cluster():
    t = generate_trace("balanced", "homog", "low", seed=1)
    before = tuple(t.cluster)
    run_trace(t, binpack)
    assert t.cluster == before
    # And a second identical run reproduces identical results.
    s1 = {k: v for k, v in run_trace(t, binpack).items() if "latency" not in k and "per_sec" not in k}
    s2 = {k: v for k, v in run_trace(t, binpack).items() if "latency" not in k and "per_sec" not in k}
    assert s1 == s2


def test_stateful_policy_gets_fresh_state_per_factory_call():
    cfg = PolicyConfig(
        name="d1",
        factory=lambda: WorkloadAwarePolicy(0.6, 0.2, 0.2),
        tunable=True,
        tuned_on="tune",
    )
    p1, p2 = cfg.factory(), cfg.factory()
    assert p1 is not p2
    assert p1.profile is not p2.profile


# --- Seed discipline -------------------------------------------------------


def test_test_split_rejects_untuned_tunables():
    bad = PolicyConfig(name="d1", factory=lambda: WorkloadAwarePolicy(0.6, 0.2, 0.2), tunable=True, tuned_on=None)
    with pytest.raises(ValueError):
        run_grid([bad], [("balanced", "homog", "low")], split="test")


def test_test_split_accepts_baselines_and_tuned_configs():
    ok = [
        PolicyConfig(name="binpack", factory=lambda: binpack),
        PolicyConfig(name="d1", factory=lambda: WorkloadAwarePolicy(0.6, 0.2, 0.2), tunable=True, tuned_on="tune"),
    ]
    records = run_grid(ok, [("balanced", "homog", "low")], split="test", seeds=[1000])
    assert len(records) == 2


def test_seeds_must_belong_to_split():
    cfg = PolicyConfig(name="binpack", factory=lambda: binpack)
    with pytest.raises(ValueError):
        run_grid([cfg], [("balanced", "homog", "low")], split="tune", seeds=[999])


def test_splits_are_disjoint():
    tune, val, test = set(SPLITS["tune"]), set(SPLITS["val"]), set(SPLITS["test"])
    assert not (tune & val) and not (tune & test) and not (val & test)


# --- Paired statistics -----------------------------------------------------


def test_paired_compare_basic():
    c = paired_compare([0.01] * 10)
    assert c.mean_delta == pytest.approx(0.01)
    assert c.wins == 10 and c.losses == 0
    assert c.ci95_low == pytest.approx(0.01)  # sd 0 -> degenerate CI


def test_paired_compare_sign_counts():
    c = paired_compare([0.02, -0.01, 0.0, 0.03, -0.02])
    assert (c.wins, c.losses, c.ties) == (2, 2, 1)


def test_paired_vs_baseline_pairs_by_seed():
    cfgs = [
        PolicyConfig(name="binpack", factory=lambda: binpack),
        PolicyConfig(name="spread", factory=lambda: spread),
    ]
    records = run_grid(cfgs, [("bimodal", "homog", "high")], split="tune", seeds=[0, 1, 2, 3, 4])
    out = paired_vs_baseline(records, baseline="binpack")
    key = ("bimodal", "homog", "high", "spread")
    assert key in out
    assert out[key].n == 5
