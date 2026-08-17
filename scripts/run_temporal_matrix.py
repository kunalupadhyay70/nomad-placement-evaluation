#!/usr/bin/env python3
"""Run the frozen Phase-4 calibration or held-out temporal matrix.

Calibration uses only tuning seeds 0--9. The held-out study uses only test
seeds 1000--1009 and is sharded by workload family so interrupted runs can be
resumed without discarding completed work.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.families import FAMILIES
from sim.matrix import PolicyConfig
from sim.policies import binpack
from sim.temporal import QUEUE_DISCIPLINE
from sim.temporal_matrix import (
    FROZEN_HYBRID_ALPHA,
    FROZEN_HYBRID_BETA,
    primary_temporal_configs,
    read_temporal_csv,
    run_temporal_grid,
    write_temporal_csv,
)
from sim.temporal_workload import DURATION_RANGE, OBSERVATION_HORIZON, TEMPORAL_LOADS

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "results" / "temporal" / "raw"
CONFIG_PATH = ROOT / "results" / "temporal" / "config.json"
CLUSTERS = ("homog", "hetero")
LOADS = ("low", "med", "high")
EXPECTED_FINAL_ROWS = len(FAMILIES) * len(CLUSTERS) * len(LOADS) * 10 * 4


def cells_for_family(family: str) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (family, cluster, load)
        for cluster in CLUSTERS
        for load in LOADS
    )


def all_cells() -> tuple[tuple[str, str, str], ...]:
    return tuple(cell for family in FAMILIES for cell in cells_for_family(family))


def write_frozen_config() -> None:
    data = {
        "phase": 4,
        "status": "frozen_before_heldout_test",
        "arrival_process": "Poisson (exponential inter-arrivals)",
        "duration_distribution": "uniform_bounded",
        "duration_min": DURATION_RANGE[0],
        "duration_max": DURATION_RANGE[1],
        "observation_horizon": OBSERVATION_HORIZON,
        "target_rho": TEMPORAL_LOADS,
        "queue_discipline": QUEUE_DISCIPLINE,
        "drain_after_horizon": True,
        "policies": [config.name for config in primary_temporal_configs()],
        "hybrid": {
            "alpha_binpack": FROZEN_HYBRID_ALPHA,
            "beta_tetris": FROZEN_HYBRID_BETA,
            "gamma_future": 0.0,
            "source": "Phase-3 tuning seeds; not retuned for Phase 4",
        },
        "calibration_seeds": list(range(10)),
        "heldout_seeds": list(range(1000, 1010)),
        "families": list(FAMILIES),
        "clusters": list(CLUSTERS),
        "loads": list(LOADS),
        "expected_primary_runs": EXPECTED_FINAL_ROWS,
    }
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def calibrate() -> None:
    records = run_temporal_grid(
        [PolicyConfig("binpack", lambda: binpack)],
        all_cells(),
        "tune",
    )
    path = RAW / "calibration.csv"
    RAW.mkdir(parents=True, exist_ok=True)
    write_temporal_csv(records, str(path))
    write_frozen_config()
    print(f"wrote {path} ({len(records)} calibration runs)")
    print(f"wrote {CONFIG_PATH}")


def run_family(family: str) -> None:
    if family not in FAMILIES:
        raise ValueError(f"unknown family {family!r}; valid: {FAMILIES}")
    records = run_temporal_grid(
        primary_temporal_configs(),
        cells_for_family(family),
        "test",
    )
    path = RAW / f"final_{family}.csv"
    RAW.mkdir(parents=True, exist_ok=True)
    write_temporal_csv(records, str(path))
    print(f"wrote {path} ({len(records)} held-out runs)")


def merge_final() -> None:
    records = []
    for family in FAMILIES:
        path = RAW / f"final_{family}.csv"
        if not path.exists():
            raise FileNotFoundError(f"missing held-out shard: {path}")
        records.extend(read_temporal_csv(str(path)))
    if len(records) != EXPECTED_FINAL_ROWS:
        raise ValueError(
            f"expected {EXPECTED_FINAL_ROWS} held-out rows, found {len(records)}"
        )
    path = RAW / "final.csv"
    write_temporal_csv(records, str(path))
    print(f"wrote {path} ({len(records)} held-out runs)")


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("calibrate")
    family_parser = subparsers.add_parser("family")
    family_parser.add_argument("family", choices=FAMILIES)
    subparsers.add_parser("merge")
    args = parser.parse_args()

    if args.command == "calibrate":
        calibrate()
    elif args.command == "family":
        run_family(args.family)
    else:
        merge_final()


if __name__ == "__main__":
    main()
