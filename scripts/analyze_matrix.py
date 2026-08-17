"""Analysis of the experiment matrix: tables + plots from CSVs only.

Reads results/matrix/{final,val,tuning,sensitivity_*}.csv and writes:

  results/matrix/comparison_admission.csv   per (family, policy) paired stats
  results/matrix/comparison_by_cell.csv     per (family, cluster, load, policy)
  results/matrix/latency.csv                per-policy decision-latency summary
  results/matrix/plots/*.png

Every table is computed from per-seed PAIRED deltas against the exact
Nomad binpack baseline on identical traces. No new simulation happens
here -- plots are generated strictly from the CSV files.
"""

from __future__ import annotations

import csv
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from scripts.run_matrix import RESULTS_DIR, _read_records
from sim.matrix import paired_compare, paired_vs_baseline

PLOTS = os.path.join(RESULTS_DIR, "plots")

POLICY_ORDER = [
    "spread", "tetris", "hybrid", "balance", "wmatch",
    "d1_cheap", "d2_capacity", "d1_cheap_nomad_ff", "d2_capacity_nomad_ff", "ff_only_cheap",
]
FAMILY_ORDER = [
    "balanced", "cpu_heavy", "ram_heavy", "bimodal",
    "tiny_large", "drift", "pred_error", "adversarial",
]


def per_family_paired(records, metric="admission_rate"):
    """family -> policy -> PairedComparison over ALL (cell, seed) pairs of
    that family (cells x seeds pooled as paired observations)."""
    by_key = {}
    for r in records:
        by_key[(r.family, r.cluster_config, r.load, r.policy, r.seed)] = getattr(r, metric)
    fams = sorted({r.family for r in records})
    pols = sorted({r.policy for r in records} - {"binpack"})
    cells = sorted({(r.family, r.cluster_config, r.load) for r in records})
    seeds = sorted({r.seed for r in records})
    out = defaultdict(dict)
    for fam in fams:
        for pol in pols:
            deltas = []
            for f, cc, load in cells:
                if f != fam:
                    continue
                for s in seeds:
                    a = by_key.get((f, cc, load, pol, s))
                    b = by_key.get((f, cc, load, "binpack", s))
                    if a is not None and b is not None:
                        deltas.append(a - b)
            if deltas:
                out[fam][pol] = paired_compare(deltas)
    return out


def main():
    os.makedirs(PLOTS, exist_ok=True)
    final = _read_records(os.path.join(RESULTS_DIR, "final.csv"))

    # ---- Table 1: per-family paired admission deltas (held-out) ----------
    fam_stats = per_family_paired(final)
    with open(os.path.join(RESULTS_DIR, "comparison_admission.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["family", "policy", "n_pairs", "mean_delta", "sd", "ci95_low", "ci95_high", "wins", "losses", "ties"])
        for fam in FAMILY_ORDER:
            for pol in POLICY_ORDER:
                c = fam_stats.get(fam, {}).get(pol)
                if c:
                    w.writerow([fam, pol, c.n, f"{c.mean_delta:.5f}", f"{c.sd_delta:.5f}",
                                f"{c.ci95_low:.5f}", f"{c.ci95_high:.5f}", c.wins, c.losses, c.ties])

    # ---- Table 2: per-cell comparisons -----------------------------------
    cell_stats = paired_vs_baseline(final, baseline="binpack")
    with open(os.path.join(RESULTS_DIR, "comparison_by_cell.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["family", "cluster", "load", "policy", "n", "mean_delta", "ci95_low", "ci95_high", "wins", "losses"])
        for (fam, cc, load, pol), c in sorted(cell_stats.items()):
            w.writerow([fam, cc, load, pol, c.n, f"{c.mean_delta:.5f}",
                        f"{c.ci95_low:.5f}", f"{c.ci95_high:.5f}", c.wins, c.losses])

    # ---- Table 3: latency -------------------------------------------------
    lat = defaultdict(list)
    p95s = defaultdict(list)
    for r in final:
        lat[r.policy].append(r.mean_latency_us)
        p95s[r.policy].append(r.p95_latency_us)
    with open(os.path.join(RESULTS_DIR, "latency.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["policy", "mean_latency_us", "p95_latency_us", "x_baseline"])
        base = statistics.fmean(lat["binpack"])
        for pol in ["binpack"] + POLICY_ORDER:
            if pol in lat:
                m = statistics.fmean(lat[pol])
                w.writerow([pol, f"{m:.1f}", f"{statistics.fmean(p95s[pol]):.1f}", f"{m / base:.2f}"])

    # ---- Plot 1: admission delta by family (headline policies) ----------
    headline = ["spread", "tetris", "hybrid", "balance", "d1_cheap", "d2_capacity"]
    x = range(len(FAMILY_ORDER))
    width = 0.13
    fig, ax = plt.subplots(figsize=(12, 5))
    for i, pol in enumerate(headline):
        means = [fam_stats.get(f, {}).get(pol).mean_delta if fam_stats.get(f, {}).get(pol) else 0 for f in FAMILY_ORDER]
        los = [means[j] - fam_stats[f][pol].ci95_low for j, f in enumerate(FAMILY_ORDER)]
        his = [fam_stats[f][pol].ci95_high - means[j] for j, f in enumerate(FAMILY_ORDER)]
        ax.bar([xi + (i - 2.5) * width for xi in x], means, width, yerr=[los, his], capsize=2, label=pol)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(FAMILY_ORDER, rotation=20)
    ax.set_ylabel("paired admission-rate delta vs. Nomad binpack")
    ax.set_title("Held-out (test seeds): paired admission delta by workload family, 95% CI")
    ax.legend(ncol=3, fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS, "admission_delta_by_family.png"), dpi=120)

    # ---- Plot 2: gamma sweep ---------------------------------------------
    sens = []
    for i in range(3):
        p = os.path.join(RESULTS_DIR, f"sensitivity_{i}.csv")
        if os.path.exists(p):
            sens.extend(_read_records(p))
    by_key = {}
    for r in sens:
        by_key[(r.family, r.cluster_config, r.load, r.policy, r.seed)] = r.admission_rate
    cells = sorted({(r.family, r.cluster_config, r.load) for r in sens})
    seeds = sorted({r.seed for r in sens})

    def mean_delta_for(pol, fams=None):
        deltas = []
        for f, cc, load in cells:
            if fams and f not in fams:
                continue
            for s in seeds:
                a, b = by_key.get((f, cc, load, pol, s)), by_key.get((f, cc, load, "binpack", s))
                if a is not None and b is not None:
                    deltas.append(a - b)
        return statistics.fmean(deltas) if deltas else None

    gammas, gvals = [], []
    for g in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6):
        d = mean_delta_for(f"sens_gamma_{g}", fams={"bimodal", "drift"})
        if d is not None:
            gammas.append(g)
            gvals.append(d)
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(gammas, gvals, marker="o")
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xlabel("gamma (future-fit weight; beta fixed at tuned value, alpha absorbs)")
    ax.set_ylabel("mean paired admission delta")
    ax.set_title("Sensitivity: gamma sweep on mixed-shape tuning cells")
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS, "sensitivity_gamma.png"), dpi=120)

    # ---- Plot 3: EWMA decay sweep ----------------------------------------
    etas, evals_ = [], []
    for eta in (0.02, 0.05, 0.1, 0.2, 0.3):
        d = mean_delta_for(f"sens_decay_{eta}", fams={"bimodal", "drift"})
        if d is not None:
            etas.append(eta)
            evals_.append(d)
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(etas, evals_, marker="o")
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xscale("log")
    ax.set_xlabel("EWMA decay eta (log scale)")
    ax.set_ylabel("mean paired admission delta")
    ax.set_title("Sensitivity: EWMA decay on mixed-shape tuning cells")
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS, "sensitivity_decay.png"), dpi=120)

    # ---- Plot 4: confidence fallback under prediction error --------------
    labels, vals = [], []
    for pol, lbl in (("sens_gamma_0.2", "fallback ON"), ("sens_no_fallback", "fallback OFF")):
        d = mean_delta_for(pol, fams={"pred_error"})
        if d is not None:
            labels.append(lbl)
            vals.append(d)
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.bar(labels, vals, color=["tab:blue", "tab:red"])
    ax.axhline(0, color="black", lw=0.8)
    ax.set_ylabel("mean paired admission delta vs. binpack")
    ax.set_title("Corrupted workload signal (pred_error):\nconfidence fallback effect")
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS, "fallback_under_pred_error.png"), dpi=120)

    # ---- Plot 5: tuning curves (alpha / lambda) --------------------------
    tuning = _read_records(os.path.join(RESULTS_DIR, "tuning.csv"))
    tcmp = paired_vs_baseline(tuning, baseline="binpack")
    agg = defaultdict(list)
    for (f, cc, load, pol), c in tcmp.items():
        agg[pol].append(c.mean_delta)
    tmean = {p: statistics.fmean(v) for p, v in agg.items()}
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    alphas = [round(0.1 * i, 1) for i in range(1, 10)]
    axes[0].plot(alphas, [tmean.get(f"hybrid_a{a}") for a in alphas], marker="o")
    axes[0].set_xlabel("alpha (Nomad weight; beta = 1 - alpha)")
    axes[0].set_title("Hybrid: alpha sweep (tune seeds)")
    lams = [0.05, 0.1, 0.2, 0.4, 0.8]
    axes[1].plot(lams, [tmean.get(f"balance_l{l}") for l in lams], marker="o", label="balance")
    axes[1].plot(lams, [tmean.get(f"wmatch_l{l}") for l in lams], marker="s", label="wmatch")
    axes[1].set_xscale("log")
    axes[1].set_xlabel("lambda (log scale)")
    axes[1].set_title("Penalty policies: lambda sweep (tune seeds)")
    axes[1].legend()
    for ax_ in axes:
        ax_.axhline(0, color="black", lw=0.8)
        ax_.set_ylabel("mean paired admission delta")
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS, "tuning_curves.png"), dpi=120)

    # ---- Plot 6: latency --------------------------------------------------
    pols = ["binpack"] + [p for p in POLICY_ORDER if p in lat]
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.bar(pols, [statistics.fmean(lat[p]) for p in pols])
    ax.set_ylabel("mean decision latency (us)")
    ax.set_title("Per-decision latency by policy (30-node cluster)")
    ax.tick_params(axis="x", rotation=30)
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS, "latency.png"), dpi=120)

    # ---- Console summary --------------------------------------------------
    print("=== held-out per-family mean paired admission delta vs binpack ===")
    hdr = "family".ljust(12) + "".join(p[:10].rjust(11) for p in headline)
    print(hdr)
    for fam in FAMILY_ORDER:
        row = fam.ljust(12)
        for pol in headline:
            c = fam_stats.get(fam, {}).get(pol)
            row += (f"{c.mean_delta:+.4f}" if c else "  --  ").rjust(11)
        print(row)
    print("\n=== latency (mean us, x binpack) ===")
    base = statistics.fmean(lat["binpack"])
    for pol in ["binpack"] + POLICY_ORDER:
        if pol in lat:
            m = statistics.fmean(lat[pol])
            print(f"  {pol:22s} {m:8.1f}  {m / base:5.2f}x")


if __name__ == "__main__":
    main()
