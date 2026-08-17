import pytest

from sim.experimental import (
    WorkloadAwarePolicy,
    WorkloadMismatchPolicy,
    make_balance_aware,
    tetris_alignment,
)
from sim.models import Job, NodeState
from sim.policies import binpack
from sim.scheduler import hypothetical_placement
from sim.workload import job_resources


def _post(node: NodeState, job: Job) -> NodeState:
    return hypothetical_placement(node, job)


def _node(total_cpu=100.0, total_ram=100.0, used_cpu=0.0, used_ram=0.0):
    return NodeState("n", total_cpu=total_cpu, total_ram=total_ram, used_cpu=used_cpu, used_ram=used_ram)


# --- Tetris alignment ------------------------------------------------------


def test_tetris_parallel_vectors_score_one():
    # Pre-placement free = (0.5, 0.5); demand = (0.05, 0.05): parallel.
    node = _node(used_cpu=50, used_ram=50)
    job = Job("j", cpu=5, ram=5)
    assert tetris_alignment(_post(node, job), job) == pytest.approx(1.0)


def test_tetris_prefers_cpu_abundant_node_for_cpu_heavy_job():
    job = Job("j", cpu=20, ram=2)
    cpu_rich = _node(used_cpu=10, used_ram=80)   # free (90, 20)
    ram_rich = _node(used_cpu=80, used_ram=10)   # free (20, 90)
    s_cpu_rich = tetris_alignment(_post(cpu_rich, job), job)
    s_ram_rich = tetris_alignment(_post(ram_rich, job), job)
    assert s_cpu_rich > s_ram_rich


class _FakeState:
    """Duck-typed stand-in to reach degenerate branches that the validated
    NodeState/Job models make unconstructible (their guards are tested in
    test_models-style assertions below)."""

    def __init__(self, total_cpu, total_ram, used_cpu, used_ram):
        self.total_cpu, self.total_ram = total_cpu, total_ram
        self.used_cpu, self.used_ram = used_cpu, used_ram


class _FakeJob:
    def __init__(self, cpu, ram):
        self.cpu, self.ram = cpu, ram


def test_tetris_zero_norm_edges_return_zero():
    # Valid models cannot express a zero free-vector or zero demand-vector
    # (NodeState forbids overcommit; Job forbids all-zero demand), so the
    # zero-norm guard is exercised with duck-typed stand-ins.
    with pytest.raises(ValueError):
        Job("bad", cpu=0, ram=0)  # the model-level guard itself
    # Zero demand norm:
    assert tetris_alignment(_FakeState(100, 100, 50, 50), _FakeJob(0, 0)) == 0.0
    # Zero free norm (node full even before the job "arrived"):
    assert tetris_alignment(_FakeState(100, 100, 100, 100), _FakeJob(0, 0)) == 0.0


def test_tetris_score_bounded():
    for used in (0, 25, 60):
        node = _node(used_cpu=used, used_ram=90 - used)
        job = Job("j", cpu=10, ram=1)
        s = tetris_alignment(_post(node, job), job)
        assert 0.0 <= s <= 1.0


def test_tetris_does_not_mutate_inputs():
    node = _node(used_cpu=30, used_ram=40)
    job = Job("j", cpu=5, ram=5)
    post = _post(node, job)
    tetris_alignment(post, job)
    assert (post.used_cpu, post.used_ram) == (35.0, 45.0)
    assert (node.used_cpu, node.used_ram) == (30.0, 40.0)


# --- Balance-aware ---------------------------------------------------------


def test_balance_aware_lam_zero_equals_binpack():
    pol = make_balance_aware(0.0)
    node = _post(_node(used_cpu=40, used_ram=70), Job("j", cpu=5, ram=5))
    job = Job("j", cpu=5, ram=5)
    assert pol(node, job) == binpack(node, job)


def test_balance_aware_penalizes_skewed_leftover():
    pol = make_balance_aware(1.0)
    job = Job("j", cpu=1, ram=1)
    balanced = _node(used_cpu=50, used_ram=50)
    skewed = _node(used_cpu=80, used_ram=20)
    # Same binpack ordering preserved, but the skewed node loses MORE
    # relative to its own binpack score than the balanced node does.
    d_bal = binpack(balanced, job) - pol(balanced, job)
    d_skew = binpack(skewed, job) - pol(skewed, job)
    assert d_skew > d_bal


def test_balance_aware_rejects_bad_lambda():
    with pytest.raises(ValueError):
        make_balance_aware(-0.1)
    with pytest.raises(ValueError):
        make_balance_aware(float("nan"))


# --- Weight validation (alpha + beta + gamma == 1) -------------------------


@pytest.mark.parametrize(
    "a,b,g",
    [(0.5, 0.5, 0.5), (1.0, 0.1, 0.0), (0.0, 0.0, 0.0), (0.4, 0.4, 0.1)],
)
def test_weights_must_sum_to_one(a, b, g):
    with pytest.raises(ValueError):
        WorkloadAwarePolicy(a, b, g)


@pytest.mark.parametrize("a,b,g", [(-0.2, 0.6, 0.6), (1.2, -0.1, -0.1)])
def test_weights_must_be_in_unit_interval(a, b, g):
    with pytest.raises(ValueError):
        WorkloadAwarePolicy(a, b, g)


def test_invalid_variant_rejected():
    with pytest.raises(ValueError):
        WorkloadAwarePolicy(0.5, 0.3, 0.2, variant="magic")


# --- Hybrid special case ---------------------------------------------------


def test_gamma_zero_is_pure_hybrid_of_binpack_and_tetris():
    pol = WorkloadAwarePolicy(0.6, 0.4, 0.0)
    node = _post(_node(used_cpu=30, used_ram=60), Job("j", cpu=8, ram=2))
    job = Job("j", cpu=8, ram=2)
    expected = 0.6 * binpack(node, job) + 0.4 * tetris_alignment(node, job)
    assert pol(node, job) == pytest.approx(expected)


def test_alpha_one_is_exact_nomad_baseline():
    pol = WorkloadAwarePolicy(1.0, 0.0, 0.0)
    node = _post(_node(used_cpu=15, used_ram=85), Job("j", cpu=3, ram=3))
    job = Job("j", cpu=3, ram=3)
    assert pol(node, job) == binpack(node, job)


# --- Confidence fallback ---------------------------------------------------


def _train(policy, jobs):
    for j in jobs:
        policy.observe(j)


def test_fresh_profile_has_zero_confidence_so_gamma_inert():
    with_ff = WorkloadAwarePolicy(0.4, 0.0, 0.6, confidence_fallback=True)
    nomad_only = WorkloadAwarePolicy(1.0, 0.0, 0.0)
    node = _post(_node(used_cpu=20, used_ram=70), Job("j", cpu=6, ram=1))
    job = Job("j", cpu=6, ram=1)
    # No observations -> confidence 0 -> gamma mass falls back to alpha.
    assert with_ff(node, job) == pytest.approx(nomad_only(node, job))


def test_consistent_observations_earn_confidence():
    pol = WorkloadAwarePolicy(0.4, 0.0, 0.6, decay=0.2)
    cpu, ram = job_resources("medium", "cpu_heavy")
    _train(pol, [Job(f"t{i}", cpu, ram, "cpu_heavy", "medium") for i in range(50)])
    assert pol.profile.confidence > 0.8


def test_erratic_observations_suppress_confidence():
    pol = WorkloadAwarePolicy(0.4, 0.0, 0.6, decay=0.2)
    # Alternate maximally-surprising classes: profile chases each, is
    # always wrong about the next.
    a = job_resources("large", "cpu_heavy")
    b = job_resources("small", "ram_heavy")
    jobs = []
    for i in range(60):
        cpu, ram = a if i % 2 == 0 else b
        jobs.append(Job(f"t{i}", cpu, ram, "cpu_heavy" if i % 2 == 0 else "ram_heavy",
                        "large" if i % 2 == 0 else "small"))
    _train(pol, jobs)
    # The alternating stream caps learnable mass at ~0.5 per bucket, so
    # confidence stays well below the consistent-stream case (> 0.8).
    assert pol.profile.confidence < 0.5


def test_fallback_disabled_uses_raw_gamma():
    pol = WorkloadAwarePolicy(0.4, 0.0, 0.6, confidence_fallback=False)
    nomad_only = WorkloadAwarePolicy(1.0, 0.0, 0.0)
    node = _post(_node(used_cpu=20, used_ram=70), Job("j", cpu=6, ram=1))
    job = Job("j", cpu=6, ram=1)
    # Even with an unwarmed (uniform) profile, gamma applies.
    assert pol(node, job) != pytest.approx(nomad_only(node, job))


# --- Future-fit variants ---------------------------------------------------


def test_cheap_future_fit_rewards_residual_that_fits_expected_jobs():
    d1 = WorkloadAwarePolicy(0.0, 0.0, 1.0, variant="cheap", decay=0.2)
    cpu, ram = job_resources("medium", "cpu_heavy")  # (20, 5)
    _train(d1, [Job(f"t{i}", cpu, ram, "cpu_heavy", "medium") for i in range(60)])
    job = Job("j", cpu=1, ram=1)
    # Residual (30 cpu, 6 ram) fits a medium cpu_heavy job; (5, 30) does not.
    fits_node = _post(_node(used_cpu=69, used_ram=93), job)
    starves_node = _post(_node(used_cpu=94, used_ram=69), job)
    assert d1(fits_node, job) > d1(starves_node, job)


def test_capacity_variant_distinguishes_how_many_fit():
    d2 = WorkloadAwarePolicy(0.0, 0.0, 1.0, variant="capacity", decay=0.2, capacity_cap=3)
    cpu, ram = job_resources("small", "balanced")  # (5, 5)
    _train(d2, [Job(f"t{i}", cpu, ram, "balanced", "small") for i in range(60)])
    job = Job("j", cpu=1, ram=1)
    roomy = _post(_node(used_cpu=79, used_ram=79), job)    # residual (20, 20): >= 3 fit
    tight = _post(_node(used_cpu=93, used_ram=93), job)    # residual (6, 6): 1 fits
    cheap = WorkloadAwarePolicy(0.0, 0.0, 1.0, variant="cheap", decay=0.2)
    _train(cheap, [Job(f"t{i}", cpu, ram, "balanced", "small") for i in range(60)])
    # Cheap variant sees both as "fits" (near-equal); capacity variant
    # must strictly prefer the roomier residual.
    assert d2(roomy, job) > d2(tight, job)
    assert cheap(roomy, job) == pytest.approx(cheap(tight, job))


# --- Workload-mismatch policy ---------------------------------------------


def test_wmatch_prefers_leftover_matching_learned_demand():
    pol = WorkloadMismatchPolicy(lam=0.5, decay=0.2)
    cpu, ram = job_resources("medium", "cpu_heavy")
    _train(pol, [Job(f"t{i}", cpu, ram, "cpu_heavy", "medium") for i in range(60)])
    job = Job("j", cpu=1, ram=1)
    # Equal binpack totals (same free sum), different leftover shapes:
    cpu_rich_leftover = _post(_node(used_cpu=39, used_ram=79), job)  # free (60, 20)
    ram_rich_leftover = _post(_node(used_cpu=79, used_ram=39), job)  # free (20, 60)
    assert pol(cpu_rich_leftover, job) > pol(ram_rich_leftover, job)


def test_wmatch_lam_zero_is_binpack():
    pol = WorkloadMismatchPolicy(lam=0.0)
    node = _post(_node(used_cpu=10, used_ram=20), Job("j", cpu=2, ram=2))
    job = Job("j", cpu=2, ram=2)
    assert pol(node, job) == binpack(node, job)
