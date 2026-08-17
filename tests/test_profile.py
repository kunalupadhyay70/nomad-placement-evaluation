import pytest

from sim.models import Job
from sim.profile import BUCKET_RESOURCES, CANONICAL_BUCKETS, WorkloadProfile, bucket_of
from sim.workload import job_resources


def _job(size, shape, i=0):
    cpu, ram = job_resources(size, shape)
    return Job(f"j-{size}-{shape}-{i}", cpu, ram, shape, size)


# --- bucket_of -------------------------------------------------------------


def test_canonical_jobs_map_exactly():
    j = _job("medium", "cpu_heavy")
    assert bucket_of(j) == "medium_cpu_heavy"


def test_unlabeled_job_snaps_to_nearest_class():
    # 20 cpu + 5 ram = total 25 (medium), cpu share 0.8 (cpu_heavy).
    j = Job("adhoc", cpu=20, ram=5)
    assert bucket_of(j) == "medium_cpu_heavy"
    # total 9 (nearest: small=10), share 0.55 (nearest: balanced).
    j2 = Job("adhoc2", cpu=5, ram=4)
    assert bucket_of(j2) == "small_balanced"


# --- profile construction --------------------------------------------------


def test_invalid_decay_rejected():
    for bad in (0.0, -0.5, 1.5):
        with pytest.raises(ValueError):
            WorkloadProfile(decay=bad)


def test_starts_uniform_with_zero_confidence():
    p = WorkloadProfile()
    assert sum(p.p.values()) == pytest.approx(1.0)
    assert len(set(p.p.values())) == 1
    assert p.confidence == 0.0
    assert p.observed == 0


# --- EWMA update math ------------------------------------------------------


def test_single_observation_update_is_exact():
    p = WorkloadProfile(decay=0.1)
    uniform = 1.0 / len(CANONICAL_BUCKETS)
    p.observe(_job("small", "balanced"))
    assert p.p["small_balanced"] == pytest.approx(0.9 * uniform + 0.1)
    assert p.p["large_ram_heavy"] == pytest.approx(0.9 * uniform)
    assert sum(p.p.values()) == pytest.approx(1.0)
    # Error measured BEFORE update: 1 - uniform.
    chance = 1.0 - uniform
    assert p.err == pytest.approx(0.9 * chance + 0.1 * (1.0 - uniform))


def test_distribution_converges_to_observed_class():
    p = WorkloadProfile(decay=0.2)
    for i in range(100):
        p.observe(_job("large", "ram_heavy", i))
    assert p.p["large_ram_heavy"] > 0.99
    assert p.confidence > 0.95


def test_profile_is_deterministic():
    a, b = WorkloadProfile(decay=0.3), WorkloadProfile(decay=0.3)
    stream = [_job("small", "cpu_heavy", i) if i % 3 else _job("medium", "balanced", i) for i in range(40)]
    for j in stream:
        a.observe(j)
        b.observe(j)
    assert a.p == b.p and a.err == b.err


# --- demand shares ---------------------------------------------------------


def test_demand_shares_reflect_learned_shape():
    p = WorkloadProfile(decay=0.2)
    for i in range(100):
        p.observe(_job("medium", "cpu_heavy", i))
    cpu_share, ram_share = p.demand_shares()
    assert cpu_share == pytest.approx(0.8, abs=0.02)
    assert cpu_share + ram_share == pytest.approx(1.0)


def test_bucket_resources_match_workload_tables():
    for b, (cpu, ram) in BUCKET_RESOURCES.items():
        assert cpu > 0 and ram > 0
