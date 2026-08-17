from pathlib import Path

import pytest

from scripts.analyze_temporal import _paired_aggregate
from sim.temporal_matrix import read_temporal_csv


def test_aggregate_intervals_use_ten_seed_blocks():
    root = Path(__file__).resolve().parents[1]
    records = read_temporal_csv(str(root / "results/temporal/raw/final.csv"))

    overall = _paired_aggregate(
        records,
        ("load",),
        lambda row: (row.load,),
    )
    high_tetris = next(
        row
        for row in overall
        if row["load"] == "high" and row["policy"] == "tetris"
    )
    assert high_tetris["n_pairs"] == 160
    assert high_tetris["n_seed_blocks"] == 10
    assert float(high_tetris["mean_delta_throughput_horizon"]) == pytest.approx(
        0.256375
    )
    assert float(high_tetris["ci95_low_delta_throughput_horizon"]) == pytest.approx(
        0.201023930,
        abs=1e-9,
    )
    assert float(high_tetris["ci95_high_delta_throughput_horizon"]) == pytest.approx(
        0.311726070,
        abs=1e-9,
    )

    family = _paired_aggregate(
        records,
        ("family", "load"),
        lambda row: (row.family, row.load),
    )
    high_ram_tetris = next(
        row
        for row in family
        if row["family"] == "ram_heavy"
        and row["load"] == "high"
        and row["policy"] == "tetris"
    )
    assert high_ram_tetris["n_pairs"] == 20
    assert high_ram_tetris["n_seed_blocks"] == 10
    assert float(
        high_ram_tetris["ci95_low_delta_throughput_horizon"]
    ) == pytest.approx(0.049467916, abs=1e-9)
