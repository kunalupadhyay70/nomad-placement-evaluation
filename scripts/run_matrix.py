"""Experiment-matrix driver: tuning, held-out evaluation, sensitivity.

Stages (run in order; each writes CSV to results/matrix/):

  python3 scripts/run_matrix.py tune_cell <0..7>       -> one tuning shard
  python3 scripts/run_matrix.py tune_select            -> tuning.csv, tuned.json
  python3 scripts/run_matrix.py val                    -> val.csv
  python3 scripts/run_matrix.py final_fam <family>     -> one held-out family shard
  python3 scripts/run_matrix.py final_merge            -> final.csv
  python3 scripts/run_matrix.py sensitivity <0..2>     -> one sensitivity shard

Seed discipline is enforced by sim.matrix.run_grid: the final stage
refuses tunable configs not marked tuned_on="tune". The tune stage
selects weights ONLY from TUNE_SEEDS on the mixed-shape tuning cells;
the merged final result covers all 48 cells on TEST_SEEDS.

Selection rule (pre-registered, see DESIGN.md): for each tunable family,
pick the config with the highest mean per-seed paired admission-rate
delta vs. the exact Nomad baseline across the tuning cells; ties break
toward the simpler config (smaller lam / larger alpha / gamma, then
lexical). No test seed is ever read before final.
"""

from __future__ import annotations

import itertools
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.experimental import WorkloadAwarePolicy, WorkloadMismatchPolicy, make_balance_aware
from sim.families import FAMILIES, LOADS
from sim.matrix import (
    PolicyConfig,
    SPLITS,
    paired_vs_baseline,
    run_grid,
    write_csv,
)
from sim.policies import binpack, spread

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results", "matrix")

# Mixed-shape cells used for tuning: the families the workload-aware
# hypothesis is ABOUT. Stationary/simple families are held out of tuning
# so we can detect overfitting to mixed shapes.
TUNE_CELLS = [
    (fam, cc, load)
    for fam in ("bimodal", "drift")
    for cc in ("homog", "hetero")
    for load in ("med", "high")
]

ALL_CELLS = [
    (fam, cc, load)
    for fam in FAMILIES
    for cc in ("homog", "hetero")
    for load in sorted(LOADS)
]

def _simplex(step: float = 0.2, gamma_max: float = 0.6):
    """All (alpha, beta, gamma) on the step-grid simplex with gamma cap."""
    k = round(1 / step)
    for i, j in itertools.product(range(k + 1), repeat=2):
        g = k - i - j
        if 0 <= g <= round(gamma_max / step):
            yield (round(i * step, 10), round(j * step, 10), round(g * step, 10))


def tuning_configs():
    cfgs = []
    # C: hybrid alpha grid (gamma = 0).
    for a in [round(0.1 * i, 1) for i in range(1, 10)]:
        cfgs.append(
            PolicyConfig(
                f"hybrid_a{a}",
                (lambda a=a: WorkloadAwarePolicy(a, round(1 - a, 10), 0.0)),
                tunable=True,
                tuned_on="tune",
                params={"family": "hybrid", "alpha": a},
            )
        )
    # Balance-aware lambda grid.
    for lam in (0.05, 0.1, 0.2, 0.4, 0.8):
        cfgs.append(
            PolicyConfig(
                f"balance_l{lam}",
                (lambda lam=lam: make_balance_aware(lam)),
                tunable=True,
                tuned_on="tune",
                params={"family": "balance", "lam": lam},
            )
        )
    # Workload-mismatch lambda grid.
    for lam in (0.05, 0.1, 0.2, 0.4, 0.8):
        cfgs.append(
            PolicyConfig(
                f"wmatch_l{lam}",
                (lambda lam=lam: WorkloadMismatchPolicy(lam)),
                tunable=True,
                tuned_on="tune",
                params={"family": "wmatch", "lam": lam},
            )
        )
    # D1/D2 simplex grids (skip pure-baseline corner alpha=1).
    for variant in ("cheap", "capacity"):
        for a, b, g in _simplex():
            if a == 1.0:
                continue
            cfgs.append(
                PolicyConfig(
                    f"d_{variant}_a{a}_b{b}_g{g}",
                    (lambda a=a, b=b, g=g, v=variant: WorkloadAwarePolicy(a, b, g, variant=v)),
                    tunable=True,
                    tuned_on="tune",
                    params={"family": f"d_{variant}", "alpha": a, "beta": b, "gamma": g},
                )
            )
    return cfgs


def stage_tune_cell(idx: int):
    """Run ONE tuning cell (all configs x tune seeds); appendable shard.

    Sharding exists because this environment limits each foreground
    command to ~45s; results are identical to a single monolithic run
    since cells are independent and traces are seed-deterministic.
    """
    os.makedirs(RESULTS_DIR, exist_ok=True)
    cfgs = [PolicyConfig("binpack", lambda: binpack)] + tuning_configs()
    cell = TUNE_CELLS[idx]
    print(f"tune shard {idx}: {cell}, {len(cfgs)} configs x {len(SPLITS['tune'])} seeds")
    records = run_grid(cfgs, [cell], split="tune")
    write_csv(records, os.path.join(RESULTS_DIR, f"tuning_cell{idx}.csv"))


def _read_records(path):
    import csv as _csv

    from sim.matrix import RunRecord

    out = []
    with open(path) as f:
        for row in _csv.DictReader(f):
            kwargs = {}
            for k, v in row.items():
                ftype = RunRecord.__dataclass_fields__[k].type
                if ftype == "int" or k in ("seed", "submitted", "placed", "rejected", "active_nodes"):
                    kwargs[k] = int(v)
                elif k in ("split", "family", "cluster_config", "load", "policy", "params"):
                    kwargs[k] = v
                else:
                    kwargs[k] = float(v)
            out.append(RunRecord(**kwargs))
    return out


def stage_tune_select():
    records = []
    for idx in range(len(TUNE_CELLS)):
        records.extend(_read_records(os.path.join(RESULTS_DIR, f"tuning_cell{idx}.csv")))
    write_csv(records, os.path.join(RESULTS_DIR, "tuning.csv"))
    cfgs = [PolicyConfig("binpack", lambda: binpack)] + tuning_configs()

    comparisons = paired_vs_baseline(records, baseline="binpack")
    # Mean paired delta across tuning cells per policy.
    per_policy: dict[str, list[float]] = {}
    for (fam, cc, load, pol), cmp_ in comparisons.items():
        per_policy.setdefault(pol, []).append(cmp_.mean_delta)
    mean_delta = {p: sum(v) / len(v) for p, v in per_policy.items()}

    by_family: dict[str, list[PolicyConfig]] = {}
    for c in cfgs[1:]:
        fam = str(c.params["family"])
        # The gamma == 0 corners of the D grids do not use the future-fit
        # term at all -- they are hybrid/tetris duplicates and are already
        # represented by those families. Selecting them as "the best D"
        # would be vacuous, so D-family selection is constrained to
        # configs with gamma > 0 (documented in DESIGN.md).
        if fam.startswith("d_") and float(c.params.get("gamma", 0.0)) == 0.0:
            continue
        by_family.setdefault(fam, []).append(c)

    chosen = {}
    for family_name, group in sorted(by_family.items()):
        # Highest mean paired delta; ties toward simplicity (larger alpha,
        # then smaller lam/gamma, then name).
        def sort_key(c: PolicyConfig):
            d = mean_delta.get(c.name, float("-inf"))
            alpha = float(c.params.get("alpha", 0.0))
            lam = float(c.params.get("lam", 0.0))
            gamma = float(c.params.get("gamma", 0.0))
            return (-d, -alpha, lam, gamma, c.name)

        best = sorted(group, key=sort_key)[0]
        chosen[family_name] = {
            "name": best.name,
            "params": best.params,
            "mean_paired_delta_vs_binpack": mean_delta.get(best.name),
        }
        print(f"  {family_name:12s} -> {best.name}  (mean paired delta {mean_delta.get(best.name):+.4f})")

    with open(os.path.join(RESULTS_DIR, "tuned.json"), "w") as f:
        json.dump(chosen, f, indent=2)


def _tuned_final_configs():
    with open(os.path.join(RESULTS_DIR, "tuned.json")) as f:
        chosen = json.load(f)

    def wa(params, variant=None):
        a, b, g = params["alpha"], params.get("beta", 0.0), params.get("gamma", 0.0)
        v = variant or "cheap"
        return lambda: WorkloadAwarePolicy(a, round(1 - a, 10) if "beta" not in params else b, g, variant=v)

    cfgs = [
        PolicyConfig("binpack", lambda: binpack),
        PolicyConfig("spread", lambda: spread),
    ]
    from sim.experimental import tetris_alignment

    cfgs.append(PolicyConfig("tetris", lambda: tetris_alignment))

    h = chosen["hybrid"]["params"]
    cfgs.append(PolicyConfig("hybrid", wa(h), tunable=True, tuned_on="tune", params=h))

    bal = chosen["balance"]["params"]
    cfgs.append(
        PolicyConfig(
            "balance",
            (lambda lam=bal["lam"]: make_balance_aware(lam)),
            tunable=True,
            tuned_on="tune",
            params=bal,
        )
    )
    wm = chosen["wmatch"]["params"]
    cfgs.append(
        PolicyConfig(
            "wmatch",
            (lambda lam=wm["lam"]: WorkloadMismatchPolicy(lam)),
            tunable=True,
            tuned_on="tune",
            params=wm,
        )
    )
    for fam_name, label in (("d_cheap", "d1_cheap"), ("d_capacity", "d2_capacity")):
        p = chosen[fam_name]["params"]
        cfgs.append(
            PolicyConfig(
                label,
                (
                    lambda a=p["alpha"], b=p["beta"], g=p["gamma"], v=fam_name.split("_")[1]: WorkloadAwarePolicy(
                        a, b, g, variant=v
                    )
                ),
                tunable=True,
                tuned_on="tune",
                params=p,
            )
        )
        # Ablations derived from the selected config (weights re-normalized):
        a, g = p["alpha"], p["gamma"]
        if g > 0:
            # Nomad + future-fit only (drop alignment).
            cfgs.append(
                PolicyConfig(
                    f"{label}_nomad_ff",
                    (
                        lambda a=a / (a + g) if a + g > 0 else 0.5,
                        g=g / (a + g) if a + g > 0 else 0.5,
                        v=fam_name.split("_")[1]: WorkloadAwarePolicy(round(a, 10), 0.0, round(g, 10), variant=v)
                    ),
                    tunable=True,
                    tuned_on="tune",
                    params={"derived_from": label},
                )
            )
    # Future-fit only ablation (fixed corner, not tuned).
    cfgs.append(
        PolicyConfig(
            "ff_only_cheap",
            lambda: WorkloadAwarePolicy(0.0, 0.0, 1.0, variant="cheap"),
            tunable=True,
            tuned_on="tune",
            params={"alpha": 0, "beta": 0, "gamma": 1},
        )
    )
    return cfgs


def stage_val():
    cfgs = _tuned_final_configs()
    print(f"val: {len(cfgs)} configs x {len(TUNE_CELLS)} cells x {len(SPLITS['val'])} seeds")
    records = run_grid(cfgs, TUNE_CELLS, split="val")
    write_csv(records, os.path.join(RESULTS_DIR, "val.csv"))


def stage_final_family(family: str):
    """Held-out shard: all cells of one workload family (see sharding note)."""
    cfgs = _tuned_final_configs()
    cells = [c for c in ALL_CELLS if c[0] == family]
    print(f"final[{family}]: {len(cfgs)} configs x {len(cells)} cells x {len(SPLITS['test'])} seeds")
    records = run_grid(cfgs, cells, split="test")
    write_csv(records, os.path.join(RESULTS_DIR, f"final_{family}.csv"))


def stage_final_merge():
    records = []
    for fam in FAMILIES:
        records.extend(_read_records(os.path.join(RESULTS_DIR, f"final_{fam}.csv")))
    write_csv(records, os.path.join(RESULTS_DIR, "final.csv"))


def stage_sensitivity():
    """Sweep one knob at a time around the chosen D config (tune split)."""
    with open(os.path.join(RESULTS_DIR, "tuned.json")) as f:
        chosen = json.load(f)
    p = chosen["d_cheap"]["params"]
    a0, b0, g0 = p["alpha"], p["beta"], p["gamma"]
    cfgs = [PolicyConfig("binpack", lambda: binpack)]
    # gamma sweep at fixed beta share; alpha absorbs the remainder.
    for g in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6):
        a = round(1.0 - b0 - g, 10)
        if a < 0:
            continue
        cfgs.append(
            PolicyConfig(
                f"sens_gamma_{g}",
                (lambda a=a, b=b0, g=g: WorkloadAwarePolicy(a, b, g, variant="cheap")),
                tunable=True,
                tuned_on="tune",
                params={"sweep": "gamma", "alpha": a, "beta": b0, "gamma": g},
            )
        )
    # EWMA decay sweep at the chosen weights.
    for eta in (0.02, 0.05, 0.1, 0.2, 0.3):
        cfgs.append(
            PolicyConfig(
                f"sens_decay_{eta}",
                (lambda a=a0, b=b0, g=g0, eta=eta: WorkloadAwarePolicy(a, b, g, variant="cheap", decay=eta)),
                tunable=True,
                tuned_on="tune",
                params={"sweep": "decay", "decay": eta},
            )
        )
    # Confidence fallback on/off under the prediction-error family.
    cfgs.append(
        PolicyConfig(
            "sens_no_fallback",
            (lambda a=a0, b=b0, g=g0: WorkloadAwarePolicy(a, b, g, variant="cheap", confidence_fallback=False)),
            tunable=True,
            tuned_on="tune",
            params={"sweep": "fallback", "fallback": False},
        )
    )
    cells = TUNE_CELLS + [("pred_error", cc, "med") for cc in ("homog", "hetero")]
    shard = int(sys.argv[2]) if len(sys.argv) > 2 else -1
    if shard >= 0:
        cells = cells[shard * 4 : (shard + 1) * 4]
    print(f"sensitivity[{shard}]: {len(cfgs)} configs x {len(cells)} cells x {len(SPLITS['tune'])} seeds")
    records = run_grid(cfgs, cells, split="tune")
    suffix = f"_{shard}" if shard >= 0 else ""
    write_csv(records, os.path.join(RESULTS_DIR, f"sensitivity{suffix}.csv"))


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "tune_select"
    os.makedirs(RESULTS_DIR, exist_ok=True)
    if stage == "tune_cell":
        stage_tune_cell(int(sys.argv[2]))
    elif stage == "final_fam":
        stage_final_family(sys.argv[2])
    else:
        {
            "tune_select": stage_tune_select,
            "val": stage_val,
            "final_merge": stage_final_merge,
            "sensitivity": stage_sensitivity,
        }[stage]()
    print("done:", stage)
