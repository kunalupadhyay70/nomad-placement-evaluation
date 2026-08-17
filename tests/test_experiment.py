import json

from sim.experiment import result_to_dict, run_experiment
from sim.policies import binpack, make_binpack_tunable, spread
from sim.workload import CANONICAL_CLASSES, generate_cluster, generate_workload


def _small_workload(seed=1):
    counts = {(size, shape): 4 for size, shape in CANONICAL_CLASSES}
    return generate_workload(counts, seed=seed)


# --- Experiment integrity ---------------------------------------------------


def test_policies_receive_identical_workload_and_cluster():
    cluster = generate_cluster(6, seed=1)
    jobs = _small_workload(seed=1)

    results = run_experiment({"binpack": binpack, "spread": spread}, cluster, jobs)

    for r in results.values():
        assert r.jobs_offered == len(jobs)
        # placed + failed must always reconcile with jobs offered
        assert r.jobs_placed + r.jobs_failed == r.jobs_offered


def test_one_policy_run_cannot_mutate_input_cluster_or_jobs():
    cluster = generate_cluster(4, seed=2)
    jobs = _small_workload(seed=2)

    cluster_before = [(n.node_id, n.total_cpu, n.total_ram, n.used_cpu, n.used_ram) for n in cluster]
    jobs_before = [(j.job_id, j.cpu, j.ram, j.shape, j.size_class) for j in jobs]

    run_experiment({"binpack": binpack, "spread": spread, "tunable-4": make_binpack_tunable(4.0)}, cluster, jobs)

    cluster_after = [(n.node_id, n.total_cpu, n.total_ram, n.used_cpu, n.used_ram) for n in cluster]
    jobs_after = [(j.job_id, j.cpu, j.ram, j.shape, j.size_class) for j in jobs]

    assert cluster_before == cluster_after
    assert jobs_before == jobs_after


def test_each_policy_starts_from_a_fresh_unmodified_cluster():
    # If policy A's run leaked state into policy B's starting cluster, the
    # two policies applied to identical workloads would not both start
    # from all-empty nodes. Verify indirectly: running binpack alone vs.
    # running binpack alongside spread produces the same binpack outcome.
    cluster = generate_cluster(5, seed=3)
    jobs = _small_workload(seed=3)

    alone = run_experiment({"binpack": binpack}, cluster, jobs)
    together = run_experiment({"binpack": binpack, "spread": spread}, cluster, jobs)

    assert alone["binpack"].jobs_placed == together["binpack"].jobs_placed
    assert alone["binpack"].jobs_failed == together["binpack"].jobs_failed
    assert alone["binpack"].final_metrics == together["binpack"].final_metrics


def test_repeated_execution_same_seed_is_identical():
    cluster = generate_cluster(5, seed=7)
    jobs = _small_workload(seed=7)

    run_a = run_experiment({"binpack": binpack}, cluster, jobs)
    run_b = run_experiment({"binpack": binpack}, cluster, jobs)

    a = run_a["binpack"]
    b = run_b["binpack"]
    assert a.jobs_placed == b.jobs_placed
    assert a.jobs_failed == b.jobs_failed
    assert a.utilization_trace == b.utilization_trace
    assert a.final_metrics == b.final_metrics
    assert a.placed_by_size_and_shape == b.placed_by_size_and_shape


def test_all_result_values_are_json_serializable():
    cluster = generate_cluster(5, seed=4)
    jobs = _small_workload(seed=4)
    results = run_experiment({"binpack": binpack, "spread": spread}, cluster, jobs)

    for r in results.values():
        d = result_to_dict(r)
        # Must not raise.
        encoded = json.dumps(d)
        assert isinstance(encoded, str)


def test_breakdown_totals_reconcile_with_global_totals():
    cluster = generate_cluster(4, seed=5)
    jobs = _small_workload(seed=5)
    results = run_experiment({"binpack": binpack}, cluster, jobs)
    r = results["binpack"]

    assert sum(r.placed_by_size.values()) == r.jobs_placed
    assert sum(r.rejected_by_size.values()) == r.jobs_failed
    assert sum(r.placed_by_shape.values()) == r.jobs_placed
    assert sum(r.rejected_by_shape.values()) == r.jobs_failed
    assert sum(r.placed_by_size_and_shape.values()) == r.jobs_placed
    assert sum(r.rejected_by_size_and_shape.values()) == r.jobs_failed
