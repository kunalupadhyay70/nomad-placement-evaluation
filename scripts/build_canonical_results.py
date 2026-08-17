"""Build the small, citable release result set from held-out raw records.

This script performs no simulation and no model selection. It validates
``results/matrix/final.csv`` and derives deterministic summary CSVs, a
manifest, and three figures from the already-held-out test split.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from scripts.run_matrix import _read_records
from sim.matrix import paired_compare

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "results" / "matrix" / "final.csv"
OUTPUT = ROOT / "results" / "canonical"
PLOTS = OUTPUT / "plots"

FAMILY_ORDER = (
    "balanced",
    "cpu_heavy",
    "ram_heavy",
    "bimodal",
    "tiny_large",
    "drift",
    "pred_error",
    "adversarial",
)
POLICY_ORDER = (
    "binpack",
    "spread",
    "tetris",
    "hybrid",
    "balance",
    "wmatch",
    "d1_cheap",
    "d2_capacity",
    "d1_cheap_nomad_ff",
    "d2_capacity_nomad_ff",
    "ff_only_cheap",
)
HEADLINE_POLICIES = (
    "binpack",
    "spread",
    "tetris",
    "hybrid",
    "wmatch",
    "d1_cheap",
    "d2_capacity",
)
PLOT_POLICIES = ("binpack", "spread", "tetris", "hybrid")
TEST_SEEDS = tuple(range(1000, 1010))
CLUSTERS = ("hetero", "homog")
LOADS = ("high", "low", "med")

COLORS = {
    "binpack": "#3B4CC0",
    "spread": "#7F7F7F",
    "tetris": "#D1495B",
    "hybrid": "#EDAE49",
    "wmatch": "#59A14F",
    "d1_cheap": "#76B7B2",
    "d2_capacity": "#B07AA1",
}


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _validate(records) -> None:
    expected_cells = {
        (family, cluster, load)
        for family in FAMILY_ORDER
        for cluster in CLUSTERS
        for load in LOADS
    }
    expected_keys = {
        (family, cluster, load, policy, seed)
        for family, cluster, load in expected_cells
        for policy in POLICY_ORDER
        for seed in TEST_SEEDS
    }
    actual_keys = [
        (r.family, r.cluster_config, r.load, r.policy, r.seed) for r in records
    ]
    if len(actual_keys) != len(set(actual_keys)):
        raise ValueError("final.csv contains duplicate experiment keys")
    if set(actual_keys) != expected_keys:
        missing = expected_keys - set(actual_keys)
        extra = set(actual_keys) - expected_keys
        raise ValueError(
            f"final.csv does not match the release matrix: missing={len(missing)}, "
            f"extra={len(extra)}"
        )
    for record in records:
        if record.split != "test":
            raise ValueError(f"non-test record found: {record}")
        if record.placed + record.rejected != record.submitted:
            raise ValueError(f"placement counts do not reconcile: {record}")
        expected_rate = record.placed / record.submitted
        if abs(record.admission_rate - expected_rate) > 1e-12:
            raise ValueError(f"admission rate does not reconcile: {record}")


def _policy_summary(records) -> list[dict[str, object]]:
    grouped = defaultdict(list)
    for record in records:
        grouped[record.policy].append(record)
    rows: list[dict[str, object]] = []
    for policy in POLICY_ORDER:
        group = grouped[policy]
        admission = [r.admission_rate for r in group]
        rows.append(
            {
                "policy": policy,
                "n_runs": len(group),
                "mean_admission_rate": f"{statistics.fmean(admission):.6f}",
                "median_admission_rate": f"{statistics.median(admission):.6f}",
                "sd_admission_rate": f"{statistics.stdev(admission):.6f}",
                "mean_active_nodes": f"{statistics.fmean(r.active_nodes for r in group):.3f}",
                "mean_cpu_util": f"{statistics.fmean(r.mean_cpu_util for r in group):.6f}",
                "mean_ram_util": f"{statistics.fmean(r.mean_ram_util for r in group):.6f}",
                "mean_free_imbalance": f"{statistics.fmean(r.free_imbalance for r in group):.6f}",
                "mean_stranded_frac": f"{statistics.fmean(r.stranded_frac for r in group):.6f}",
            }
        )
    return rows


def _policy_by_workload(records) -> list[dict[str, object]]:
    grouped = defaultdict(list)
    for record in records:
        if record.policy in HEADLINE_POLICIES:
            grouped[(record.family, record.policy)].append(record)
    rows: list[dict[str, object]] = []
    for family in FAMILY_ORDER:
        for policy in HEADLINE_POLICIES:
            group = grouped[(family, policy)]
            admission = [r.admission_rate for r in group]
            rows.append(
                {
                    "family": family,
                    "policy": policy,
                    "n_runs": len(group),
                    "mean_admission_rate": f"{statistics.fmean(admission):.6f}",
                    "median_admission_rate": f"{statistics.median(admission):.6f}",
                    "sd_admission_rate": f"{statistics.stdev(admission):.6f}",
                    "mean_active_nodes": f"{statistics.fmean(r.active_nodes for r in group):.3f}",
                }
            )
    return rows


def _paired_comparisons(records) -> list[dict[str, object]]:
    by_key = {
        (r.family, r.cluster_config, r.load, r.policy, r.seed): r.admission_rate
        for r in records
    }
    rows: list[dict[str, object]] = []
    for family in FAMILY_ORDER:
        for policy in HEADLINE_POLICIES:
            if policy == "binpack":
                continue
            deltas = []
            for cluster in CLUSTERS:
                for load in LOADS:
                    for seed in TEST_SEEDS:
                        candidate = by_key[(family, cluster, load, policy, seed)]
                        baseline = by_key[(family, cluster, load, "binpack", seed)]
                        deltas.append(candidate - baseline)
            comparison = paired_compare(deltas)
            rows.append(
                {
                    "family": family,
                    "policy": policy,
                    "baseline": "binpack",
                    "n_pairs": comparison.n,
                    "mean_delta": f"{comparison.mean_delta:.6f}",
                    "median_delta": f"{statistics.median(deltas):.6f}",
                    "sd_delta": f"{comparison.sd_delta:.6f}",
                    "ci95_low": f"{comparison.ci95_low:.6f}",
                    "ci95_high": f"{comparison.ci95_high:.6f}",
                    "wins": comparison.wins,
                    "losses": comparison.losses,
                    "ties": comparison.ties,
                }
            )
    return rows


def _plot_admission(policy_rows: list[dict[str, object]]) -> None:
    lookup = {
        (str(row["family"]), str(row["policy"])): float(row["mean_admission_rate"])
        for row in policy_rows
    }
    policies = ("binpack", "tetris", "hybrid")
    x = list(range(len(FAMILY_ORDER)))
    width = 0.25
    fig, ax = plt.subplots(figsize=(12, 5.5))
    for index, policy in enumerate(policies):
        values = [100 * lookup[(family, policy)] for family in FAMILY_ORDER]
        positions = [value + (index - 1) * width for value in x]
        ax.bar(positions, values, width, label=policy, color=COLORS[policy])
    ax.set_ylim(88, 100)
    ax.set_xticks(x)
    ax.set_xticklabels(FAMILY_ORDER, rotation=20, ha="right")
    ax.set_ylabel("mean admission rate (%)")
    ax.set_title("Held-out admission by workload family (detail view: 88–100%)")
    ax.legend(frameon=False, ncol=3)
    ax.grid(axis="y", alpha=0.2)
    fig.tight_layout()
    fig.savefig(PLOTS / "admission_by_workload.png", dpi=160)
    plt.close(fig)


def _plot_tetris_delta(paired_rows: list[dict[str, object]]) -> None:
    rows = [row for row in paired_rows if row["policy"] == "tetris"]
    lookup = {str(row["family"]): row for row in rows}
    means = [100 * float(lookup[family]["mean_delta"]) for family in FAMILY_ORDER]
    low = [100 * float(lookup[family]["ci95_low"]) for family in FAMILY_ORDER]
    high = [100 * float(lookup[family]["ci95_high"]) for family in FAMILY_ORDER]
    errors = [
        [mean - lo for mean, lo in zip(means, low)],
        [hi - mean for mean, hi in zip(means, high)],
    ]
    fig, ax = plt.subplots(figsize=(9, 5.5))
    y = list(range(len(FAMILY_ORDER)))
    colors = [COLORS["tetris"] if mean >= 0 else "#555555" for mean in means]
    ax.errorbar(means, y, xerr=errors, fmt="none", ecolor="#333333", capsize=4, lw=1.3)
    ax.scatter(means, y, c=colors, s=55, zorder=3)
    ax.axvline(0, color="black", lw=0.9)
    ax.set_yticks(y)
    ax.set_yticklabels(FAMILY_ORDER)
    ax.invert_yaxis()
    ax.set_xlabel("Tetris − Nomad binpack admission (percentage points, 95% CI)")
    ax.set_title("Held-out paired admission effect of Tetris placement")
    ax.grid(axis="x", alpha=0.2)
    fig.tight_layout()
    fig.savefig(PLOTS / "tetris_delta.png", dpi=160)
    plt.close(fig)


def _plot_tradeoff(summary_rows: list[dict[str, object]]) -> None:
    lookup = {str(row["policy"]): row for row in summary_rows}
    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    for policy in PLOT_POLICIES:
        row = lookup[policy]
        x = float(row["mean_active_nodes"])
        y = 100 * float(row["mean_admission_rate"])
        ax.scatter(x, y, s=80, color=COLORS[policy], label=policy)
        ax.annotate(policy, (x, y), xytext=(5, 5), textcoords="offset points", fontsize=9)
    ax.set_xlim(20, 30.5)
    ax.set_ylim(94.8, 96.7)
    ax.set_xlabel("mean active nodes (30-node clusters; lower = more consolidation)")
    ax.set_ylabel("mean admission rate (%)")
    ax.set_title("Admission–consolidation trade-off (held-out runs, detail view)")
    ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(PLOTS / "admission_vs_consolidation.png", dpi=160)
    plt.close(fig)


def main() -> None:
    records = _read_records(str(SOURCE))
    _validate(records)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    PLOTS.mkdir(parents=True, exist_ok=True)

    summary_rows = _policy_summary(records)
    workload_rows = _policy_by_workload(records)
    paired_rows = _paired_comparisons(records)

    _write_csv(OUTPUT / "summary.csv", list(summary_rows[0]), summary_rows)
    _write_csv(OUTPUT / "policy_by_workload.csv", list(workload_rows[0]), workload_rows)
    _write_csv(OUTPUT / "paired_comparison.csv", list(paired_rows[0]), paired_rows)

    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    manifest = {
        "source": "results/matrix/final.csv",
        "source_sha256": source_hash,
        "split": "test",
        "raw_policy_runs": len(records),
        "workload_families": len(FAMILY_ORDER),
        "cluster_configurations": len(CLUSTERS),
        "load_levels": len(LOADS),
        "experiment_cells": len(FAMILY_ORDER) * len(CLUSTERS) * len(LOADS),
        "seeds_per_cell": len(TEST_SEEDS),
        "seed_range": [min(TEST_SEEDS), max(TEST_SEEDS)],
        "policies_and_ablations": len(POLICY_ORDER),
        "timing_excluded_from_canonical_claims": True,
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    _plot_admission(workload_rows)
    _plot_tetris_delta(paired_rows)
    _plot_tradeoff(summary_rows)
    print(f"Validated {len(records)} held-out policy runs from {SOURCE.relative_to(ROOT)}")
    print(f"Wrote canonical tables, manifest, and plots to {OUTPUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
