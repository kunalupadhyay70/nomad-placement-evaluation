#!/usr/bin/env python3
"""Run the corrected single-seed smoke experiment and write results/smoke_results.json.

METHODOLOGY NOTE (see METHODOLOGY_CORRECTION_REPORT.md): this replaces the
previous `scripts/run_baseline.py`. This run is intentionally called a
"smoke experiment" / "single-seed integration experiment", not a
"baseline" -- one seed, one cluster, one workload composition establishes
an *observed outcome for this configuration*, not a general policy
advantage. It is useful for validating that the full pipeline (workload
generation -> scheduling -> metrics -> reporting) works end to end.

Usage:
    PYTHONPATH=. python3 scripts/run_smoke_experiment.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sim.experiment import run_experiment
from sim.policies import binpack, make_binpack_tunable, spread
from sim.workload import CANONICAL_CLASSES, generate_cluster, generate_workload

N_NODES = 24
SEED = 42
JOBS_PER_CLASS = 20  # every one of the 9 canonical (size, shape) classes gets this many jobs
# Chosen so this smoke run exercises both outcomes (most jobs placed, a
# meaningful minority rejected) rather than either trivially succeeding or
# exhausting the cluster completely -- see METHODOLOGY_CORRECTION_REPORT.md.

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")


def _cluster_to_json(cluster):
    return [
        {
            "node_id": n.node_id,
            "total_cpu": n.total_cpu,
            "total_ram": n.total_ram,
            "used_cpu": n.used_cpu,
            "used_ram": n.used_ram,
        }
        for n in cluster
    ]


def _jobs_to_json(jobs):
    return [
        {
            "job_id": j.job_id,
            "size_class": j.size_class,
            "shape": j.shape,
            "cpu": j.cpu,
            "ram": j.ram,
        }
        for j in jobs
    ]


def _decisions_to_json(decisions):
    return [
        {
            "job_id": d.job.job_id,
            "node_id": d.node_id,
            "score": d.score,
            "feasible_node_count": d.feasible_node_count,
            "tied_candidate_count": d.tied_candidate_count,
            "placed": d.placed,
        }
        for d in decisions
    ]


def _metrics_to_json(m):
    return {
        "node_count": m.node_count,
        "active_nodes": m.active_nodes,
        "idle_nodes": m.idle_nodes,
        "mean_cpu_utilization": m.mean_cpu_utilization,
        "mean_ram_utilization": m.mean_ram_utilization,
        "cpu_imbalance": m.cpu_imbalance,
        "ram_imbalance": m.ram_imbalance,
        "cpu_free_fraction_gap": m.cpu_free_fraction_gap,
        "ram_free_fraction_gap": m.ram_free_fraction_gap,
        "cpu_dispersion": m.cpu_dispersion,
        "ram_dispersion": m.ram_dispersion,
    }


def _make_plots(results, size_shape_counts) -> None:
    names = list(results.keys())

    # --- utilization_over_time.png ---
    plt.figure(figsize=(8, 5))
    for name, r in results.items():
        cpu_trace = [t[0] for t in r.utilization_trace]
        plt.plot(range(1, len(cpu_trace) + 1), cpu_trace, label=name)
    plt.xlabel("Jobs scheduled (arrival order)")
    plt.ylabel("Mean cluster CPU utilization")
    plt.title(f"CPU utilization over time -- single-seed smoke experiment (seed={SEED})")
    plt.legend()
    plt.tight_layout()
    p1 = os.path.join(RESULTS_DIR, "utilization_over_time.png")
    plt.savefig(p1, dpi=150)
    plt.close()
    print(f"Wrote {p1}")

    # --- final_utilization.png ---
    cpu_final = [results[n].final_metrics.mean_cpu_utilization for n in names]
    ram_final = [results[n].final_metrics.mean_ram_utilization for n in names]
    x = range(len(names))
    width = 0.35
    plt.figure(figsize=(7, 5))
    plt.bar([i - width / 2 for i in x], cpu_final, width, label="CPU")
    plt.bar([i + width / 2 for i in x], ram_final, width, label="RAM")
    plt.xticks(list(x), names)
    plt.ylabel("Final mean utilization")
    plt.title("Final utilization by policy -- single-seed smoke experiment")
    plt.legend()
    plt.tight_layout()
    p2 = os.path.join(RESULTS_DIR, "final_utilization.png")
    plt.savefig(p2, dpi=150)
    plt.close()
    print(f"Wrote {p2}")

    # --- placement_failures.png ---
    failures = [results[n].jobs_failed for n in names]
    plt.figure(figsize=(7, 5))
    plt.bar(names, failures, color="tab:red")
    plt.ylabel("Jobs failed to place (this run only)")
    plt.title("Placement failures by policy -- single-seed smoke experiment")
    plt.tight_layout()
    p3 = os.path.join(RESULTS_DIR, "placement_failures.png")
    plt.savefig(p3, dpi=150)
    plt.close()
    print(f"Wrote {p3}")

    # --- residual_schedulability.png ---
    # Operational metric: how many of the final cluster's nodes could still
    # fit one more job of each canonical class. This is NOT the dispersion
    # metric -- it directly answers "can this cluster still take job X".
    class_names = [f"{size}_{shape}" for size, shape in sorted(size_shape_counts.keys())]
    fig, ax = plt.subplots(figsize=(12, 5))
    bar_width = 0.8 / len(names)
    x = list(range(len(class_names)))
    for i, name in enumerate(names):
        counts = [results[name].nodes_fitting_class[c] for c in class_names]
        offsets = [xi + i * bar_width for xi in x]
        ax.bar(offsets, counts, width=bar_width, label=name)
    ax.set_xticks([xi + bar_width * (len(names) - 1) / 2 for xi in x])
    ax.set_xticklabels(class_names, rotation=45, ha="right")
    ax.set_ylabel("Nodes that could fit one more job of this class")
    ax.set_title("Residual schedulability by job class (final cluster state)")
    ax.legend()
    plt.tight_layout()
    p4 = os.path.join(RESULTS_DIR, "residual_schedulability.png")
    plt.savefig(p4, dpi=150)
    plt.close()
    print(f"Wrote {p4}")


def main() -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)

    cluster = generate_cluster(N_NODES, seed=SEED)
    size_shape_counts = {(size, shape): JOBS_PER_CLASS for size, shape in CANONICAL_CLASSES}
    jobs = generate_workload(size_shape_counts, seed=SEED, shuffle=True)

    policies = {
        "binpack": binpack,
        "spread": spread,
        "tunable-base-4": make_binpack_tunable(4.0),
    }

    results = run_experiment(policies, cluster, jobs)

    output = {
        "phase": "Phase Two (corrected)",
        "status": "single-seed smoke experiment -- pipeline validation only, not a general policy comparison",
        "experiment_type": "single-seed integration experiment",
        "limitation": (
            "These values describe one deterministic configuration (one seed, "
            "one cluster, one workload composition, one arrival order) and must "
            "not be generalized across workloads or arrival orders. See "
            "METHODOLOGY_CORRECTION_REPORT.md and README.md 'Limitations'."
        ),
        "config": {
            "n_nodes": N_NODES,
            "seed": SEED,
            "jobs_per_class": JOBS_PER_CLASS,
            "total_jobs": len(jobs),
        },
        "workload_distribution": {
            f"{size}_{shape}": count for (size, shape), count in size_shape_counts.items()
        },
        "cluster": _cluster_to_json(cluster),
        "jobs_in_arrival_order": _jobs_to_json(jobs),
        "policies": {},
    }

    for name, r in results.items():
        output["policies"][name] = {
            "jobs_offered": r.jobs_offered,
            "jobs_placed": r.jobs_placed,
            "jobs_failed": r.jobs_failed,
            "score_tie_count": r.score_tie_count,
            "decisions": _decisions_to_json(r.decisions),
            "final_nodes": _cluster_to_json(r.final_cluster),
            "final_metrics": _metrics_to_json(r.final_metrics),
            "placed_by_size": r.placed_by_size,
            "rejected_by_size": r.rejected_by_size,
            "placed_by_shape": r.placed_by_shape,
            "rejected_by_shape": r.rejected_by_shape,
            "placed_by_size_and_shape": r.placed_by_size_and_shape,
            "rejected_by_size_and_shape": r.rejected_by_size_and_shape,
            "nodes_fitting_class": r.nodes_fitting_class,
            "additional_jobs_placeable": r.additional_jobs_placeable,
        }

    out_path = os.path.join(RESULTS_DIR, "smoke_results.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Wrote {out_path}")

    _make_plots(results, size_shape_counts)

    print("\nSummary (single seed, not a general conclusion):")
    for name, r in results.items():
        m = r.final_metrics
        print(
            f"  {name:16s} placed={r.jobs_placed:3d} failed={r.jobs_failed:3d} "
            f"ties={r.score_tie_count:3d} cpu_util={m.mean_cpu_utilization:.3f} "
            f"ram_util={m.mean_ram_utilization:.3f} cpu_dispersion={m.cpu_dispersion:.3f} "
            f"cpu_free_gap={m.cpu_free_fraction_gap:.3f}"
        )


if __name__ == "__main__":
    main()
