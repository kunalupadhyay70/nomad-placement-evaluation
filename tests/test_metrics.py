import dataclasses

import pytest

from sim.metrics import compute_class_fit_counts, compute_metrics, nodes_fitting
from sim.models import NodeState


def _node(node_id, total_cpu=100, total_ram=100, used_cpu=0, used_ram=0):
    return NodeState(node_id, total_cpu=total_cpu, total_ram=total_ram, used_cpu=used_cpu, used_ram=used_ram)


# --- basic utilization / imbalance (unchanged behavior) --------------------


def test_metrics_on_empty_cluster_usage():
    cluster = [_node("n1"), _node("n2")]
    m = compute_metrics(cluster)
    assert m.mean_cpu_utilization == 0.0
    assert m.mean_ram_utilization == 0.0
    assert m.active_nodes == 0
    assert m.idle_nodes == 2
    assert m.cpu_imbalance == 0.0


def test_metrics_mean_utilization():
    cluster = [_node("n1", used_cpu=50, used_ram=25), _node("n2", used_cpu=100, used_ram=75)]
    m = compute_metrics(cluster)
    assert m.mean_cpu_utilization == pytest.approx(0.75)
    assert m.mean_ram_utilization == pytest.approx(0.5)
    assert m.active_nodes == 2
    assert m.idle_nodes == 0


def test_metrics_imbalance_zero_when_uniform():
    cluster = [_node("n1", used_cpu=50, used_ram=50), _node("n2", used_cpu=50, used_ram=50)]
    m = compute_metrics(cluster)
    assert m.cpu_imbalance == 0.0
    assert m.ram_imbalance == 0.0


def test_metrics_imbalance_positive_when_skewed():
    cluster = [_node("n1", used_cpu=100, used_ram=100), _node("n2", used_cpu=0, used_ram=0)]
    m = compute_metrics(cluster)
    assert m.cpu_imbalance > 0
    assert m.ram_imbalance > 0


def test_metrics_raises_on_empty_cluster():
    with pytest.raises(ValueError):
        compute_metrics([])


# --- residual free-fraction balance gap ------------------------------------


def test_free_fraction_gap_zero_when_uniform():
    cluster = [_node("n1", used_cpu=50, used_ram=50), _node("n2", used_cpu=50, used_ram=50)]
    m = compute_metrics(cluster)
    assert m.cpu_free_fraction_gap == pytest.approx(0.0)
    assert m.ram_free_fraction_gap == pytest.approx(0.0)


def test_free_fraction_gap_positive_when_nodes_differ():
    cluster = [_node("n1", used_cpu=0), _node("n2", used_cpu=100)]
    m = compute_metrics(cluster)
    # n1 free_cpu_fraction=1.0, n2 free_cpu_fraction=0.0 -> gap = 1.0
    assert m.cpu_free_fraction_gap == pytest.approx(1.0)


# --- residual-capacity dispersion (renamed from "fragmentation") -----------


def test_dispersion_lower_when_free_capacity_concentrated_on_one_node():
    # All free CPU capacity sits on a single node -> low dispersion.
    concentrated = [_node("n1", used_cpu=100), _node("n2", used_cpu=0)]
    m = compute_metrics(concentrated)
    assert m.cpu_dispersion == pytest.approx(0.0)


def test_dispersion_higher_when_free_capacity_scattered_across_nodes():
    scattered = [_node("n1", used_cpu=50), _node("n2", used_cpu=50)]
    concentrated = [_node("n1", used_cpu=100), _node("n2", used_cpu=0)]

    m_scattered = compute_metrics(scattered)
    m_concentrated = compute_metrics(concentrated)

    assert m_scattered.cpu_dispersion > m_concentrated.cpu_dispersion


def test_dispersion_increases_as_free_capacity_spreads_across_more_nodes():
    two_way = [_node(f"n{i}", used_cpu=50) for i in range(2)]  # each 50% free
    four_way = [_node(f"n{i}", used_cpu=75) for i in range(4)]  # each 25% free, same total free
    m2 = compute_metrics(two_way)
    m4 = compute_metrics(four_way)
    assert m4.cpu_dispersion > m2.cpu_dispersion


def test_dispersion_zero_total_free_capacity_is_defined_as_zero():
    full = [_node("n1", used_cpu=100, used_ram=100), _node("n2", used_cpu=100, used_ram=100)]
    m = compute_metrics(full)
    # Documented convention: no free capacity -> dispersion defined as 0.0,
    # not NaN or a divide-by-zero error.
    assert m.cpu_dispersion == 0.0
    assert m.ram_dispersion == 0.0


def test_cpu_and_ram_dispersion_are_independent():
    # CPU free capacity concentrated on n1, RAM free capacity concentrated on n2.
    cluster = [
        _node("n1", used_cpu=0, used_ram=100),
        _node("n2", used_cpu=100, used_ram=0),
    ]
    m = compute_metrics(cluster)
    assert m.cpu_dispersion == pytest.approx(0.0)
    assert m.ram_dispersion == pytest.approx(0.0)


def test_metrics_field_names_use_dispersion_not_fragmentation():
    fields = {f.name for f in dataclasses.fields(compute_metrics([_node("n1")]))}
    assert "cpu_dispersion" in fields
    assert "ram_dispersion" in fields
    assert not any("fragmentation" in name for name in fields)


# --- operational schedulability ---------------------------------------------


def test_nodes_fitting_counts_exact_fit_as_feasible():
    node = _node("n1", total_cpu=10, total_ram=10, used_cpu=0, used_ram=0)
    # Exactly consumes all remaining capacity -> still feasible.
    assert nodes_fitting([node], cpu_demand=10, ram_demand=10) == 1


def test_nodes_fitting_distinguishes_cpu_and_ram_limited_cases():
    cpu_limited = _node("n1", total_cpu=10, total_ram=100, used_cpu=5, used_ram=0)  # 5 free cpu, 100 free ram
    ram_limited = _node("n2", total_cpu=100, total_ram=10, used_cpu=0, used_ram=5)  # 100 free cpu, 5 free ram

    # Needs 6 cpu, 1 ram: cpu_limited node can't fit (only 5 free cpu), ram_limited can.
    assert nodes_fitting([cpu_limited], cpu_demand=6, ram_demand=1) == 0
    assert nodes_fitting([ram_limited], cpu_demand=6, ram_demand=1) == 1

    # Needs 1 cpu, 6 ram: cpu_limited can fit, ram_limited can't.
    assert nodes_fitting([cpu_limited], cpu_demand=1, ram_demand=6) == 1
    assert nodes_fitting([ram_limited], cpu_demand=1, ram_demand=6) == 0


def test_compute_class_fit_counts_matches_manual_hand_calculation():
    cluster = [
        _node("n1", total_cpu=10, total_ram=10, used_cpu=0, used_ram=0),  # 10 free / 10 free
        _node("n2", total_cpu=10, total_ram=10, used_cpu=8, used_ram=0),  # 2 free / 10 free
        _node("n3", total_cpu=10, total_ram=10, used_cpu=10, used_ram=10),  # 0 free / 0 free
    ]
    class_resources = {
        "small_balanced": (5.0, 5.0),  # n1 fits, n2 doesn't (cpu), n3 doesn't
        "small_cpu_heavy": (2.0, 1.0),  # n1 fits, n2 fits (2<=2, 1<=10), n3 doesn't
    }
    counts = compute_class_fit_counts(cluster, class_resources)
    assert counts == {"small_balanced": 1, "small_cpu_heavy": 2}
