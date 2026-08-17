#!/usr/bin/env python3
"""Independent Phase-4 raw-data and headline-statistics verifier.

This module deliberately does not import ``scripts.analyze_temporal`` or the
simulation package. It treats the held-out CSV as its only statistical input,
validates the predeclared matrix and metric arithmetic, and recomputes paired
comparisons two ways:

* the original interval over every family/cluster/seed row; and
* the release interval over ten seed-block macro-averages.

The latter is the approved inferential unit because seed IDs recur across
workload and cluster strata. Means remain equal-weight macro-averages over the
predeclared strata. Per-run P95 values are averaged; job-level waits are not
pooled because the raw matrix contains run summaries rather than job ledgers.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW = ROOT / "results" / "temporal" / "raw" / "final.csv"
DEFAULT_CALIBRATION = ROOT / "results" / "temporal" / "raw" / "calibration.csv"
EXPECTED_RAW_SHA256 = "8f46c90029b46ce23c23b22ca1b45a388628830bcea80d3b359b066a3090fcb1"

FAMILIES = (
    "balanced",
    "cpu_heavy",
    "ram_heavy",
    "bimodal",
    "tiny_large",
    "drift",
    "pred_error",
    "adversarial",
)
CLUSTERS = ("homog", "hetero")
LOADS = ("low", "med", "high")
POLICIES = ("binpack", "spread", "tetris", "hybrid")
TEST_SEEDS = tuple(range(1000, 1010))
TUNE_SEEDS = tuple(range(10))

STRING_FIELDS = {"split", "family", "cluster_config", "load", "policy", "params"}
INT_FIELDS = {
    "seed",
    "submitted",
    "feasible_jobs",
    "permanently_infeasible",
    "started_by_horizon",
    "completed_by_horizon",
    "running_at_horizon",
    "queued_at_horizon",
    "horizon_active_nodes",
}
OPTIONAL_FIELDS = {
    "admission_ratio_horizon",
    "completion_ratio_horizon",
    "mean_wait_time",
    "p95_wait_time",
    "mean_turnaround_time",
    "p95_turnaround_time",
    "mean_slowdown",
    "p95_slowdown",
    "makespan",
    "drained_throughput",
}
METRICS = (
    "throughput_horizon",
    "p95_wait_time",
    "queued_at_horizon",
    "time_weighted_active_nodes",
)
METRIC_DIRECTIONS = {
    "throughput_horizon": "higher_is_better",
    "p95_wait_time": "lower_is_better",
    "queued_at_horizon": "lower_is_better",
    "time_weighted_active_nodes": "lower_is_better",
}

# Two-sided 0.975 Student-t quantiles. Constants avoid adding SciPy solely for
# three fixed, predeclared sample sizes.
T_CRITICAL_95 = {
    10: 2.2621571627409915,  # df=9, approved seed-block interval
    20: 2.093024054408263,  # df=19, original family/load interval
    160: 1.9750920721372103,  # df=159, original load interval
}

Row = dict[str, object]
IndexKey = tuple[str, str, str, int, str]


def _read_csv(path: Path) -> list[Row]:
    rows: list[Row] = []
    with path.open(newline="") as handle:
        for raw in csv.DictReader(handle):
            row: Row = {}
            for name, value in raw.items():
                if name in STRING_FIELDS:
                    row[name] = value
                elif name in INT_FIELDS:
                    row[name] = int(value)
                elif value == "":
                    if name not in OPTIONAL_FIELDS:
                        raise ValueError(f"unexpected empty field {name!r} in {path}")
                    row[name] = None
                else:
                    parsed = float(value)
                    if not math.isfinite(parsed):
                        raise ValueError(f"non-finite {name!r} in {path}")
                    row[name] = parsed
            rows.append(row)
    return rows


def _number(row: Row, name: str) -> float:
    value = row[name]
    if value is None:
        raise ValueError(f"required metric {name!r} is undefined")
    return float(value)


def _close(actual: float, expected: float, tolerance: float = 1e-12) -> None:
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=tolerance):
        raise AssertionError(f"expected {expected:.15g}, found {actual:.15g}")


def _mean_ci(values: Sequence[float]) -> dict[str, float | int]:
    n = len(values)
    if n not in T_CRITICAL_95:
        raise ValueError(f"unsupported predeclared sample size: {n}")
    mean = statistics.fmean(values)
    sd = statistics.stdev(values)
    half = T_CRITICAL_95[n] * sd / math.sqrt(n)
    return {
        "n": n,
        "mean": mean,
        "sd": sd,
        "ci95_low": mean - half,
        "ci95_high": mean + half,
    }


def _validate_calibration(rows: Sequence[Row]) -> None:
    expected = {
        (family, cluster, load, seed, "binpack")
        for family in FAMILIES
        for cluster in CLUSTERS
        for load in LOADS
        for seed in TUNE_SEEDS
    }
    actual = [
        (
            str(row["family"]),
            str(row["cluster_config"]),
            str(row["load"]),
            int(row["seed"]),
            str(row["policy"]),
        )
        for row in rows
    ]
    if len(actual) != 480 or len(actual) != len(set(actual)) or set(actual) != expected:
        raise AssertionError("calibration matrix is incomplete, duplicated, or unexpected")
    if {row["split"] for row in rows} != {"tune"}:
        raise AssertionError("calibration contains a non-tune split")


def _validate_heldout(rows: Sequence[Row]) -> dict[IndexKey, Row]:
    expected = {
        (family, cluster, load, seed, policy)
        for family in FAMILIES
        for cluster in CLUSTERS
        for load in LOADS
        for seed in TEST_SEEDS
        for policy in POLICIES
    }
    keys: list[IndexKey] = [
        (
            str(row["family"]),
            str(row["cluster_config"]),
            str(row["load"]),
            int(row["seed"]),
            str(row["policy"]),
        )
        for row in rows
    ]
    if len(keys) != 1920 or len(keys) != len(set(keys)) or set(keys) != expected:
        raise AssertionError("held-out matrix is incomplete, duplicated, or unexpected")
    if {row["split"] for row in rows} != {"test"}:
        raise AssertionError("held-out matrix contains a non-test split")

    shared_by_trace: dict[tuple[str, str, str, int], tuple[object, ...]] = {}
    for key, row in zip(keys, rows):
        family, cluster, load, seed, _ = key
        total = int(row["submitted"])
        feasible = int(row["feasible_jobs"])
        permanent = int(row["permanently_infeasible"])
        started = int(row["started_by_horizon"])
        completed = int(row["completed_by_horizon"])
        running = int(row["running_at_horizon"])
        queued = int(row["queued_at_horizon"])
        horizon = _number(row, "observation_horizon")
        if total != feasible + permanent:
            raise AssertionError(f"feasibility counts do not reconcile: {key}")
        if total != completed + running + queued + permanent:
            raise AssertionError(f"horizon states do not reconcile: {key}")
        if started != completed + running:
            raise AssertionError(f"start counts do not reconcile: {key}")
        _close(_number(row, "throughput_horizon"), completed / horizon)
        _close(_number(row, "admission_ratio_horizon"), started / feasible)
        _close(_number(row, "completion_ratio_horizon"), completed / feasible)
        _close(
            _number(row, "drained_throughput"),
            feasible / _number(row, "makespan"),
        )

        fingerprint = (
            total,
            feasible,
            permanent,
            row["target_rho"],
            row["realized_rho_cpu"],
            row["realized_rho_ram"],
            row["realized_rho"],
            row["arrival_rate"],
            row["observation_horizon"],
            row["duration_min"],
            row["duration_max"],
        )
        trace_key = (family, cluster, load, seed)
        previous = shared_by_trace.setdefault(trace_key, fingerprint)
        if previous != fingerprint:
            raise AssertionError(f"policy trace metadata differs: {trace_key}")
    return dict(zip(keys, rows))


def _paired_values(
    index: dict[IndexKey, Row],
    policy: str,
    load: str,
    metric: str,
    family: str | None = None,
) -> list[tuple[str, str, int, float]]:
    families: Iterable[str] = (family,) if family is not None else FAMILIES
    return [
        (
            selected_family,
            cluster,
            seed,
            _number(index[(selected_family, cluster, load, seed, policy)], metric)
            - _number(index[(selected_family, cluster, load, seed, "binpack")], metric),
        )
        for selected_family in families
        for cluster in CLUSTERS
        for seed in TEST_SEEDS
    ]


def _comparison(
    index: dict[IndexKey, Row],
    policy: str,
    load: str,
    metric: str,
    family: str | None = None,
) -> dict[str, object]:
    pairs = _paired_values(index, policy, load, metric, family)
    all_pair = _mean_ci([delta for _, _, _, delta in pairs])
    seed_blocks = [
        statistics.fmean(delta for _, _, item_seed, delta in pairs if item_seed == seed)
        for seed in TEST_SEEDS
    ]
    return {
        "all_pairs_interval": all_pair,
        "seed_block_interval": _mean_ci(seed_blocks),
        "seed_block_values": seed_blocks,
    }


def _policy_mean(index: dict[IndexKey, Row], policy: str, load: str, metric: str) -> float:
    return statistics.fmean(
        _number(index[(family, cluster, load, seed, policy)], metric)
        for family in FAMILIES
        for cluster in CLUSTERS
        for seed in TEST_SEEDS
    )


def _build_results(rows: Sequence[Row], index: dict[IndexKey, Row]) -> dict[str, object]:
    load_results: dict[str, object] = {}
    for load in LOADS:
        policy_means = {
            policy: {
                metric: _policy_mean(index, policy, load, metric)
                for metric in METRICS
            }
            for policy in POLICIES
        }
        comparisons = {
            policy: {
                metric: _comparison(index, policy, load, metric)
                for metric in METRICS
            }
            for policy in POLICIES
            if policy != "binpack"
        }
        binpack_mean = float(policy_means["binpack"]["throughput_horizon"])
        tetris_mean = float(policy_means["tetris"]["throughput_horizon"])
        per_pair_percentages = []
        for family in FAMILIES:
            for cluster in CLUSTERS:
                for seed in TEST_SEEDS:
                    baseline = _number(
                        index[(family, cluster, load, seed, "binpack")],
                        "throughput_horizon",
                    )
                    candidate = _number(
                        index[(family, cluster, load, seed, "tetris")],
                        "throughput_horizon",
                    )
                    if baseline > 0:
                        per_pair_percentages.append(100 * (candidate - baseline) / baseline)
        load_results[load] = {
            "policy_means": policy_means,
            "comparisons_vs_binpack": comparisons,
            "policy_rankings": {
                metric: {
                    "direction": METRIC_DIRECTIONS[metric],
                    "order": sorted(
                        POLICIES,
                        key=lambda item: float(policy_means[item][metric]),
                        reverse=METRIC_DIRECTIONS[metric] == "higher_is_better",
                    ),
                }
                for metric in METRICS
            },
            "throughput_ranking": sorted(
                POLICIES,
                key=lambda item: float(policy_means[item]["throughput_horizon"]),
                reverse=True,
            ),
            "tetris_relative_throughput_percent_ratio_of_means": (
                100 * (tetris_mean - binpack_mean) / binpack_mean
            ),
            "tetris_mean_of_per_pair_throughput_percentages": statistics.fmean(
                per_pair_percentages
            ),
        }

    family_high = {
        family: _comparison(
            index,
            "tetris",
            "high",
            "throughput_horizon",
            family,
        )
        for family in FAMILIES
    }
    return {
        "aggregation": (
            "equal-weight macro-average over predeclared workload/cluster strata; "
            "paired candidate-minus-binpack deltas; 10 seed-block t interval (df=9)"
        ),
        "p95_estimand": "macro-average of per-run nearest-rank drained-job P95 values",
        "raw_rows": len(rows),
        "load_results": load_results,
        "family_high_tetris_vs_binpack": family_high,
    }


def _assert_headlines(results: dict[str, object]) -> None:
    loads = results["load_results"]
    assert isinstance(loads, dict)

    expected = {
        ("low", "tetris", "throughput_horizon"): (0.0, 0.0, 0.0),
        ("low", "tetris", "p95_wait_time"): (
            0.126720863275530,
            0.089220377143435,
            0.164221349407625,
        ),
        ("low", "tetris", "time_weighted_active_nodes"): (
            5.918121766824820,
            5.434460053946372,
            6.401783479703267,
        ),
        ("med", "tetris", "throughput_horizon"): (
            -0.035875,
            -0.052961453150389,
            -0.018788546849611,
        ),
        ("med", "tetris", "p95_wait_time"): (
            0.690466436162577,
            0.500985496067309,
            0.879947376257845,
        ),
        ("med", "tetris", "time_weighted_active_nodes"): (
            1.619770112261901,
            1.460806410382328,
            1.778733814141475,
        ),
        ("high", "tetris", "throughput_horizon"): (
            0.256375,
            0.201023930359266,
            0.311726069640734,
        ),
        ("high", "tetris", "p95_wait_time"): (
            0.528720346488138,
            0.303647956884593,
            0.753792736091683,
        ),
        ("high", "tetris", "queued_at_horizon"): (
            -9.40625,
            -11.642957100251683,
            -7.169542899748317,
        ),
        ("high", "tetris", "time_weighted_active_nodes"): (
            1.194212794834296,
            1.110423585994955,
            1.278002003673637,
        ),
        ("high", "hybrid", "throughput_horizon"): (
            0.16175,
            0.127040545952857,
            0.196459454047143,
        ),
        ("high", "hybrid", "time_weighted_active_nodes"): (
            0.856632865789149,
            0.791506484004891,
            0.921759247573406,
        ),
    }
    for (load, policy, metric), values in expected.items():
        comparison = loads[load]["comparisons_vs_binpack"][policy][metric]
        interval = comparison["seed_block_interval"]
        for actual, wanted in zip(
            (interval["mean"], interval["ci95_low"], interval["ci95_high"]),
            values,
        ):
            _close(float(actual), wanted, 1e-12)
    _close(
        float(loads["high"]["tetris_relative_throughput_percent_ratio_of_means"]),
        1.3811168797936852,
        1e-12,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw", nargs="?", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument("--expected-sha256", default=EXPECTED_RAW_SHA256)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    digest = hashlib.sha256(args.raw.read_bytes()).hexdigest()
    if digest != args.expected_sha256:
        raise AssertionError(
            f"raw SHA-256 mismatch: expected {args.expected_sha256}, found {digest}"
        )
    calibration = _read_csv(args.calibration)
    heldout = _read_csv(args.raw)
    _validate_calibration(calibration)
    if {int(row["seed"]) for row in calibration} & {
        int(row["seed"]) for row in heldout
    }:
        raise AssertionError("calibration and held-out seeds overlap")
    index = _validate_heldout(heldout)
    results = _build_results(heldout, index)
    results["raw_sha256"] = digest
    results["calibration_rows"] = len(calibration)
    _assert_headlines(results)

    rendered = json.dumps(results, indent=2, sort_keys=True) + "\n"
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
