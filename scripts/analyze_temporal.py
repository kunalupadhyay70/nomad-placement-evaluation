#!/usr/bin/env python3
"""Validate and summarize the frozen held-out Phase-4 temporal results.

This script performs no simulation or parameter selection. It derives all
canonical CSVs, confidence intervals, figures, and the manifest from the raw
1,920-row held-out matrix.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sim.families import FAMILIES
from sim.temporal_matrix import TemporalRunRecord, read_temporal_csv

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "results" / "temporal" / "raw" / "final.csv"
OUTPUT = ROOT / "results" / "temporal" / "canonical"
PLOTS = OUTPUT / "plots"

POLICIES = ("binpack", "spread", "tetris", "hybrid")
CLUSTERS = ("homog", "hetero")
LOADS = ("low", "med", "high")
TEST_SEEDS = tuple(range(1000, 1010))
COLORS = {
    "binpack": "#3B4CC0",
    "spread": "#7F7F7F",
    "tetris": "#D1495B",
    "hybrid": "#EDAE49",
}

Metric = tuple[str, Callable[[TemporalRunRecord], float]]
METRICS: tuple[Metric, ...] = (
    ("throughput_horizon", lambda row: row.throughput_horizon),
    ("p95_wait_time", lambda row: _required(row.p95_wait_time, "p95_wait_time")),
    ("mean_wait_time", lambda row: _required(row.mean_wait_time, "mean_wait_time")),
    ("queued_at_horizon", lambda row: float(row.queued_at_horizon)),
    ("completion_ratio_horizon", lambda row: _required(row.completion_ratio_horizon, "completion_ratio_horizon")),
    ("time_weighted_cpu_utilization", lambda row: row.time_weighted_cpu_utilization),
    ("time_weighted_ram_utilization", lambda row: row.time_weighted_ram_utilization),
    ("time_weighted_queue_length", lambda row: row.time_weighted_queue_length),
    ("time_weighted_active_nodes", lambda row: row.time_weighted_active_nodes),
    ("drained_throughput", lambda row: _required(row.drained_throughput, "drained_throughput")),
)


def _required(value: float | None, name: str) -> float:
    if value is None:
        raise ValueError(f"held-out drained run has undefined {name}")
    return value


def _write_csv(path: Path, rows: Sequence[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"no rows for {path}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(rows[0]),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _t_critical_95(n: int) -> float:
    # Exact two-sided Student-t value for the ten predeclared seed blocks.
    exact = {10: 2.2621571627409915}
    if n in exact:
        return exact[n]
    if n >= 30:
        return 1.96
    raise ValueError(f"no predeclared 95% t critical value for n={n}")


def _stats(values: Sequence[float]) -> tuple[float, float, float, float]:
    if len(values) < 2:
        raise ValueError("at least two samples are required")
    mean = statistics.fmean(values)
    sd = statistics.stdev(values)
    half = _t_critical_95(len(values)) * sd / math.sqrt(len(values))
    return mean, sd, mean - half, mean + half


def _fmt(value: float) -> str:
    return f"{value:.9f}"


def _validate(records: Sequence[TemporalRunRecord]) -> None:
    expected = {
        (family, cluster, load, policy, seed)
        for family in FAMILIES
        for cluster in CLUSTERS
        for load in LOADS
        for policy in POLICIES
        for seed in TEST_SEEDS
    }
    keys = [
        (row.family, row.cluster_config, row.load, row.policy, row.seed)
        for row in records
    ]
    if len(keys) != len(set(keys)):
        raise ValueError("raw temporal results contain duplicate experiment keys")
    if set(keys) != expected:
        raise ValueError(
            f"raw temporal matrix mismatch: missing={len(expected - set(keys))}, "
            f"extra={len(set(keys) - expected)}"
        )

    trace_fields: dict[tuple[str, str, str, int], tuple[object, ...]] = {}
    for row in records:
        if row.split != "test":
            raise ValueError("canonical input contains a non-test split")
        if row.submitted != row.feasible_jobs + row.permanently_infeasible:
            raise ValueError(f"submitted count does not reconcile: {row}")
        if row.submitted != (
            row.completed_by_horizon
            + row.running_at_horizon
            + row.queued_at_horizon
            + row.permanently_infeasible
        ):
            raise ValueError(f"horizon states do not reconcile: {row}")
        if row.started_by_horizon != row.completed_by_horizon + row.running_at_horizon:
            raise ValueError(f"started count does not reconcile: {row}")
        if not math.isclose(
            row.throughput_horizon,
            row.completed_by_horizon / row.observation_horizon,
            abs_tol=1e-12,
        ):
            raise ValueError(f"throughput does not reconcile: {row}")
        if row.completion_ratio_horizon is None or not math.isclose(
            row.completion_ratio_horizon,
            row.completed_by_horizon / row.feasible_jobs,
            abs_tol=1e-12,
        ):
            raise ValueError(f"completion ratio does not reconcile: {row}")
        for name, getter in METRICS:
            if not math.isfinite(getter(row)):
                raise ValueError(f"non-finite {name}: {row}")

        trace_key = (row.family, row.cluster_config, row.load, row.seed)
        fingerprint = (
            row.submitted,
            row.feasible_jobs,
            row.permanently_infeasible,
            row.target_rho,
            row.realized_rho_cpu,
            row.realized_rho_ram,
            row.arrival_rate,
            row.observation_horizon,
            row.duration_min,
            row.duration_max,
        )
        previous = trace_fields.setdefault(trace_key, fingerprint)
        if previous != fingerprint:
            raise ValueError(f"policies did not receive identical trace metadata: {trace_key}")


def _summary(
    records: Iterable[TemporalRunRecord],
    key_names: Sequence[str],
    key: Callable[[TemporalRunRecord], tuple[str, ...]],
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, ...], list[TemporalRunRecord]] = defaultdict(list)
    for row in records:
        grouped[key(row)].append(row)
    rows: list[dict[str, object]] = []
    for group_key in sorted(grouped):
        group = grouped[group_key]
        output: dict[str, object] = dict(zip(key_names, group_key))
        output["n_runs"] = len(group)
        output["n_seed_blocks"] = len({row.seed for row in group})
        for name, getter in METRICS:
            by_seed: dict[int, list[float]] = defaultdict(list)
            for row in group:
                by_seed[row.seed].append(getter(row))
            if set(by_seed) != set(TEST_SEEDS):
                raise ValueError(f"summary seed-block mismatch: {group_key}")
            seed_blocks = [statistics.fmean(by_seed[seed]) for seed in TEST_SEEDS]
            mean, sd, low, high = _stats(seed_blocks)
            output[f"mean_{name}"] = _fmt(mean)
            output[f"sd_{name}"] = _fmt(sd)
            output[f"ci95_low_{name}"] = _fmt(low)
            output[f"ci95_high_{name}"] = _fmt(high)
        rows.append(output)
    return rows


def _paired(records: Sequence[TemporalRunRecord]) -> list[dict[str, object]]:
    indexed = {
        (row.family, row.cluster_config, row.load, row.policy, row.seed): row
        for row in records
    }
    rows: list[dict[str, object]] = []
    for family in FAMILIES:
        for cluster in CLUSTERS:
            for load in LOADS:
                for policy in POLICIES:
                    if policy == "binpack":
                        continue
                    output: dict[str, object] = {
                        "family": family,
                        "cluster_config": cluster,
                        "load": load,
                        "policy": policy,
                        "baseline": "binpack",
                        "n_pairs": len(TEST_SEEDS),
                        "n_seed_blocks": len(TEST_SEEDS),
                    }
                    for name, getter in METRICS:
                        deltas = []
                        for seed in TEST_SEEDS:
                            candidate = indexed[(family, cluster, load, policy, seed)]
                            baseline = indexed[(family, cluster, load, "binpack", seed)]
                            deltas.append(getter(candidate) - getter(baseline))
                        mean, sd, low, high = _stats(deltas)
                        output[f"mean_delta_{name}"] = _fmt(mean)
                        output[f"sd_delta_{name}"] = _fmt(sd)
                        output[f"ci95_low_delta_{name}"] = _fmt(low)
                        output[f"ci95_high_delta_{name}"] = _fmt(high)
                        output[f"wins_{name}"] = sum(value > 1e-12 for value in deltas)
                        output[f"losses_{name}"] = sum(value < -1e-12 for value in deltas)
                        output[f"ties_{name}"] = sum(abs(value) <= 1e-12 for value in deltas)
                    rows.append(output)
    return rows


def _paired_aggregate(
    records: Sequence[TemporalRunRecord],
    key_names: Sequence[str],
    key: Callable[[TemporalRunRecord], tuple[str, ...]],
) -> list[dict[str, object]]:
    indexed = {
        (row.family, row.cluster_config, row.load, row.policy, row.seed): row
        for row in records
    }
    grouped: dict[tuple[str, ...], dict[str, dict[int, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    for row in records:
        if row.policy == "binpack":
            continue
        baseline = indexed[
            (row.family, row.cluster_config, row.load, "binpack", row.seed)
        ]
        group_key = (*key(row), row.policy)
        for name, getter in METRICS:
            grouped[group_key][name][row.seed].append(
                getter(row) - getter(baseline)
            )

    output_rows: list[dict[str, object]] = []
    for group_key in sorted(grouped):
        output: dict[str, object] = dict(zip((*key_names, "policy"), group_key))
        output["baseline"] = "binpack"
        samples = grouped[group_key]
        first_metric = next(iter(samples.values()))
        output["n_pairs"] = sum(len(values) for values in first_metric.values())
        output["n_seed_blocks"] = len(first_metric)
        for name, _ in METRICS:
            by_seed = samples[name]
            if set(by_seed) != set(TEST_SEEDS):
                raise ValueError(f"paired seed-block mismatch: {group_key}")
            values = [value for seed in TEST_SEEDS for value in by_seed[seed]]
            seed_blocks = [statistics.fmean(by_seed[seed]) for seed in TEST_SEEDS]
            mean, sd, low, high = _stats(seed_blocks)
            output[f"mean_delta_{name}"] = _fmt(mean)
            output[f"sd_delta_{name}"] = _fmt(sd)
            output[f"ci95_low_delta_{name}"] = _fmt(low)
            output[f"ci95_high_delta_{name}"] = _fmt(high)
            output[f"wins_{name}"] = sum(value > 1e-12 for value in values)
            output[f"losses_{name}"] = sum(value < -1e-12 for value in values)
            output[f"ties_{name}"] = sum(abs(value) <= 1e-12 for value in values)
        output_rows.append(output)
    return output_rows


def _lookup(rows: Sequence[dict[str, object]]) -> dict[tuple[str, str, str], dict[str, object]]:
    return {
        (str(row["family"]), str(row["load"]), str(row["policy"])): row
        for row in rows
    }


def _facet_plot(
    family_load_rows: Sequence[dict[str, object]],
    metric: str,
    ylabel: str,
    title: str,
    filename: str,
) -> None:
    lookup = _lookup(family_load_rows)
    x = range(len(LOADS))
    fig, axes = plt.subplots(2, 4, figsize=(15, 8.2), sharex=True)
    for ax, family in zip(axes.flat, FAMILIES):
        for policy in POLICIES:
            means = [float(lookup[(family, load, policy)][f"mean_{metric}"]) for load in LOADS]
            lows = [float(lookup[(family, load, policy)][f"ci95_low_{metric}"]) for load in LOADS]
            highs = [float(lookup[(family, load, policy)][f"ci95_high_{metric}"]) for load in LOADS]
            errors = [
                [mean - low for mean, low in zip(means, lows)],
                [high - mean for mean, high in zip(means, highs)],
            ]
            ax.errorbar(
                x,
                means,
                yerr=errors,
                marker="o",
                linewidth=1.5,
                markersize=3.5,
                capsize=2,
                color=COLORS[policy],
                label=policy,
            )
        ax.set_title(family.replace("_", " "))
        ax.set_xticks(list(x), LOADS)
        ax.grid(alpha=0.2)
    axes[0, 0].set_ylabel(ylabel)
    axes[1, 0].set_ylabel(ylabel)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.suptitle(title, y=0.985)
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.955),
        ncol=4,
        frameon=False,
    )
    fig.text(
        0.5,
        0.015,
        "offered-load region (20 runs; bars are 95% t CIs over 10 seed blocks)",
        ha="center",
    )
    fig.tight_layout(rect=(0, 0.04, 1, 0.90))
    fig.savefig(PLOTS / filename, dpi=160)
    plt.close(fig)


def _tradeoff_plot(overall_rows: Sequence[dict[str, object]]) -> None:
    lookup = {
        (str(row["load"]), str(row["policy"])): row for row in overall_rows
    }
    fig, ax = plt.subplots(figsize=(8.5, 6))
    markers = {"low": "o", "med": "s", "high": "^"}
    for policy in POLICIES:
        for load in LOADS:
            row = lookup[(load, policy)]
            x = float(row["mean_time_weighted_active_nodes"])
            y = float(row["mean_throughput_horizon"])
            xerr = [
                [x - float(row["ci95_low_time_weighted_active_nodes"])],
                [float(row["ci95_high_time_weighted_active_nodes"]) - x],
            ]
            yerr = [
                [y - float(row["ci95_low_throughput_horizon"])],
                [float(row["ci95_high_throughput_horizon"]) - y],
            ]
            ax.errorbar(
                x,
                y,
                xerr=xerr,
                yerr=yerr,
                fmt=markers[load],
                markersize=7,
                capsize=2,
                color=COLORS[policy],
            )
            ax.annotate(load, (x, y), xytext=(4, 3), textcoords="offset points", fontsize=8)
        # One legend entry per policy.
        ax.plot([], [], marker="o", linestyle="", color=COLORS[policy], label=policy)
    ax.set_xlabel("time-weighted active nodes")
    ax.set_ylabel("completed jobs per simulated time unit")
    ax.set_title(
        "Temporal throughput–consolidation trade-off\n"
        "(160 runs; 95% t CIs over 10 seed blocks)"
    )
    ax.grid(alpha=0.2)
    ax.legend(frameon=False, ncol=2)
    fig.tight_layout()
    fig.savefig(PLOTS / "throughput_vs_active_nodes.png", dpi=160)
    plt.close(fig)


def _backlog_plot(overall_rows: Sequence[dict[str, object]]) -> None:
    lookup = {
        (str(row["load"]), str(row["policy"])): row for row in overall_rows
    }
    x = list(range(len(LOADS)))
    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    for policy in POLICIES:
        values = [float(lookup[(load, policy)]["mean_queued_at_horizon"]) for load in LOADS]
        lows = [float(lookup[(load, policy)]["ci95_low_queued_at_horizon"]) for load in LOADS]
        highs = [float(lookup[(load, policy)]["ci95_high_queued_at_horizon"]) for load in LOADS]
        errors = [
            [mean - low for mean, low in zip(values, lows)],
            [high - mean for mean, high in zip(values, highs)],
        ]
        ax.errorbar(
            x,
            values,
            yerr=errors,
            marker="o",
            capsize=3,
            color=COLORS[policy],
            label=policy,
        )
    ax.set_xticks(x, LOADS)
    ax.set_xlabel("offered-load region")
    ax.set_ylabel("mean jobs queued at observation horizon")
    ax.set_title(
        "Backlog growth by offered load\n"
        "(160 runs; 95% t CIs over 10 seed blocks)"
    )
    ax.grid(alpha=0.2)
    ax.legend(frameon=False, ncol=4)
    fig.tight_layout()
    fig.savefig(PLOTS / "backlog_by_load.png", dpi=160)
    plt.close(fig)


def main() -> None:
    records = read_temporal_csv(str(SOURCE))
    _validate(records)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    PLOTS.mkdir(parents=True, exist_ok=True)

    cell_rows = _summary(
        records,
        ("family", "cluster_config", "load", "policy"),
        lambda row: (row.family, row.cluster_config, row.load, row.policy),
    )
    family_load_rows = _summary(
        records,
        ("family", "load", "policy"),
        lambda row: (row.family, row.load, row.policy),
    )
    overall_rows = _summary(
        records,
        ("load", "policy"),
        lambda row: (row.load, row.policy),
    )
    paired_rows = _paired(records)
    paired_family_load_rows = _paired_aggregate(
        records,
        ("family", "load"),
        lambda row: (row.family, row.load),
    )
    paired_overall_load_rows = _paired_aggregate(
        records,
        ("load",),
        lambda row: (row.load,),
    )
    _write_csv(OUTPUT / "cell_summary.csv", cell_rows)
    _write_csv(OUTPUT / "family_load_summary.csv", family_load_rows)
    _write_csv(OUTPUT / "overall_load_summary.csv", overall_rows)
    _write_csv(OUTPUT / "paired_vs_binpack.csv", paired_rows)
    _write_csv(
        OUTPUT / "paired_family_load_vs_binpack.csv", paired_family_load_rows
    )
    _write_csv(
        OUTPUT / "paired_overall_load_vs_binpack.csv", paired_overall_load_rows
    )

    _facet_plot(
        family_load_rows,
        "throughput_horizon",
        "throughput (jobs / time unit)",
        "Held-out throughput versus offered load",
        "throughput_by_family_load.png",
    )
    _facet_plot(
        family_load_rows,
        "p95_wait_time",
        "P95 wait (simulated time units)",
        "Held-out drained-job P95 waiting time versus offered load",
        "p95_wait_by_family_load.png",
    )
    _tradeoff_plot(overall_rows)
    _backlog_plot(overall_rows)

    source_bytes = SOURCE.read_bytes()
    manifest = {
        "phase": 4,
        "source": str(SOURCE.relative_to(ROOT)),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "raw_rows": len(records),
        "families": len(FAMILIES),
        "clusters": len(CLUSTERS),
        "loads": len(LOADS),
        "heldout_seeds_per_cell": len(TEST_SEEDS),
        "policies": list(POLICIES),
        "queue_discipline": "fifo_scan_backfill",
        "confidence_interval": (
            "two-sided 95% Student-t interval over 10 seed-block "
            "macro-averages (df=9)"
        ),
        "aggregation": (
            "equal-weight macro-average over predeclared workload-family and "
            "cluster strata within each seed"
        ),
        "p95_aggregation": (
            "macro-average of per-run nearest-rank drained-job P95 values; "
            "jobs are not pooled across runs"
        ),
        "multiple_comparisons": (
            "family-level intervals are exploratory and are not adjusted for "
            "multiple comparisons"
        ),
        "outputs": [
            "cell_summary.csv",
            "family_load_summary.csv",
            "overall_load_summary.csv",
            "paired_vs_binpack.csv",
            "paired_family_load_vs_binpack.csv",
            "paired_overall_load_vs_binpack.csv",
            "plots/throughput_by_family_load.png",
            "plots/p95_wait_by_family_load.png",
            "plots/throughput_vs_active_nodes.png",
            "plots/backlog_by_load.png",
        ],
    }
    (OUTPUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    print(f"validated {len(records)} held-out rows from {SOURCE}")
    print(f"wrote canonical artifacts to {OUTPUT}")


if __name__ == "__main__":
    main()
