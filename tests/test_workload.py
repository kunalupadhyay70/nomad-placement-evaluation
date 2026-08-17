import random

import pytest

from sim.workload import (
    CANONICAL_CLASSES,
    SHAPES,
    SIZES,
    class_key,
    generate_cluster,
    generate_workload,
    job_resources,
)

# --- Size / shape separation ------------------------------------------------


def test_every_shape_has_correct_cpu_ram_ratio():
    assert SHAPES["balanced"].cpu_share == pytest.approx(0.50)
    assert SHAPES["balanced"].ram_share == pytest.approx(0.50)
    assert SHAPES["cpu_heavy"].cpu_share == pytest.approx(0.80)
    assert SHAPES["cpu_heavy"].ram_share == pytest.approx(0.20)
    assert SHAPES["ram_heavy"].cpu_share == pytest.approx(0.20)
    assert SHAPES["ram_heavy"].ram_share == pytest.approx(0.80)
    for shape in SHAPES.values():
        assert shape.cpu_share + shape.ram_share == pytest.approx(1.0)


def test_every_size_class_has_documented_total():
    assert SIZES["small"] == 10.0
    assert SIZES["medium"] == 25.0
    assert SIZES["large"] == 40.0


def test_shape_does_not_alter_total_size():
    for size in SIZES:
        totals = set()
        for shape in SHAPES:
            cpu, ram = job_resources(size, shape)
            totals.add(round(cpu + ram, 9))
        # All three shapes at a given size must sum to the same total.
        assert len(totals) == 1
        assert totals.pop() == pytest.approx(SIZES[size])


def test_size_does_not_alter_cpu_ram_ratio():
    for shape, spec in SHAPES.items():
        ratios = set()
        for size in SIZES:
            cpu, ram = job_resources(size, shape)
            ratios.add(round(cpu / (cpu + ram), 9))
        assert len(ratios) == 1
        assert ratios.pop() == pytest.approx(spec.cpu_share)


def test_all_nine_combinations_exist():
    assert len(CANONICAL_CLASSES) == 9
    assert set(CANONICAL_CLASSES) == {
        (size, shape) for size in SIZES for shape in SHAPES
    }


def test_job_resources_rejects_unknown_size():
    with pytest.raises(ValueError):
        job_resources("huge", "balanced")


def test_job_resources_rejects_unknown_shape():
    with pytest.raises(ValueError):
        job_resources("small", "diagonal")


# --- generate_workload --------------------------------------------------


def test_generate_workload_produces_exact_requested_counts():
    counts = {
        ("small", "balanced"): 10,
        ("medium", "cpu_heavy"): 10,
        ("large", "ram_heavy"): 5,
    }
    jobs = generate_workload(counts, seed=1)
    assert len(jobs) == 25

    tally = {}
    for j in jobs:
        tally[(j.size_class, j.shape)] = tally.get((j.size_class, j.shape), 0) + 1
    assert tally == counts


def test_generate_workload_job_resources_match_class():
    counts = {("medium", "cpu_heavy"): 4}
    jobs = generate_workload(counts, seed=1)
    expected_cpu, expected_ram = job_resources("medium", "cpu_heavy")
    for j in jobs:
        assert j.cpu == pytest.approx(expected_cpu)
        assert j.ram == pytest.approx(expected_ram)
        assert j.size_class == "medium"
        assert j.shape == "cpu_heavy"


def test_generate_workload_rejects_unknown_size():
    with pytest.raises(ValueError):
        generate_workload({("huge", "balanced"): 1}, seed=1)


def test_generate_workload_rejects_unknown_shape():
    with pytest.raises(ValueError):
        generate_workload({("small", "diagonal"): 1}, seed=1)


def test_generate_workload_rejects_negative_count():
    with pytest.raises(ValueError):
        generate_workload({("small", "balanced"): -1}, seed=1)


def test_generate_workload_job_ids_unique_and_deterministic():
    counts = {("small", "balanced"): 5, ("large", "ram_heavy"): 5}
    jobs_a = generate_workload(counts, seed=1, shuffle=False)
    jobs_b = generate_workload(counts, seed=1, shuffle=False)
    ids_a = [j.job_id for j in jobs_a]
    assert len(ids_a) == len(set(ids_a))  # unique
    assert ids_a == [j.job_id for j in jobs_b]  # deterministic


def test_generate_workload_same_seed_same_order():
    counts = {(size, shape): 3 for size, shape in CANONICAL_CLASSES}
    jobs_a = generate_workload(counts, seed=42)
    jobs_b = generate_workload(counts, seed=42)
    assert [j.job_id for j in jobs_a] == [j.job_id for j in jobs_b]


def test_generate_workload_different_seeds_normally_reorder():
    counts = {(size, shape): 3 for size, shape in CANONICAL_CLASSES}
    jobs_a = generate_workload(counts, seed=1)
    jobs_b = generate_workload(counts, seed=2)
    assert [j.job_id for j in jobs_a] != [j.job_id for j in jobs_b]


def test_generate_workload_does_not_shuffle_when_disabled():
    counts = {("small", "balanced"): 3}
    jobs = generate_workload(counts, seed=1, shuffle=False)
    assert [j.job_id for j in jobs] == [
        "job-small-balanced-0000",
        "job-small-balanced-0001",
        "job-small-balanced-0002",
    ]


def test_generate_workload_does_not_touch_global_random_state():
    random.seed(12345)
    state_before = random.getstate()

    counts = {(size, shape): 2 for size, shape in CANONICAL_CLASSES}
    generate_workload(counts, seed=999, shuffle=True)

    state_after = random.getstate()
    assert state_before == state_after


# --- cluster generation (unchanged behavior, still covered) --------------


def test_generate_cluster_is_deterministic_for_same_seed():
    cluster_a = generate_cluster(10, seed=3)
    cluster_b = generate_cluster(10, seed=3)
    assert [(n.node_id, n.total_cpu, n.total_ram) for n in cluster_a] == [
        (n.node_id, n.total_cpu, n.total_ram) for n in cluster_b
    ]


def test_generate_cluster_nodes_start_empty():
    cluster = generate_cluster(5, seed=1)
    assert len(cluster) == 5
    assert all(n.used_cpu == 0 and n.used_ram == 0 for n in cluster)


def test_class_key_format():
    assert class_key("small", "balanced") == "small_balanced"
