"""Workload families and cluster configurations for the experiment matrix.

Every family is generated from the corrected (size_class x shape) class
system in sim/workload.py -- no new job kinds are introduced. A family
specifies:

- a class mix (weights over (size_class, shape) pairs), or two mixes for
  the drift family,
- an ordering rule (shuffled / half-shuffled / constructed adversarial),
- an observation transform (identity for all families except the
  prediction-error family, which feeds the policy a shape-flipped view of
  each job while the scheduler sees the real job).

Offered load
------------
The job COUNT for a trace is derived from a target offered load rho,
defined against the generated cluster:

    rho = max( n * m_cpu / C_cpu ,  n * m_ram / C_ram )

where m_cpu/m_ram are the mix's mean per-job demands and C_cpu/C_ram the
cluster's aggregate capacity. Given rho we set
n = floor(rho / max(m_cpu/C_cpu, m_ram/C_ram)). rho > 1 guarantees
rejections, so admission rate discriminates between policies.

Per-class counts use largest-remainder rounding so counts sum exactly
to n and the realized mix is as close to the weights as integers allow.

Determinism: everything below is a pure function of
(family, cluster_config, load, seed).

Adversarial ordering (W8) is a HEURISTIC construction, not a proven
worst case: jobs are emitted largest-first, and within each size band
shapes alternate cpu_heavy / ram_heavy (balanced jobs last). Large
skewed jobs land first on an empty cluster where every node scores
similarly, and each alternation steers the residual shape away from the
next job's need -- awkward leftovers accumulate early instead of being
absorbed by small jobs.

Prediction-error family (W7): the real workload is CPU-biased
(70% cpu_heavy / 30% balanced by count), but the policy's `observe`
stream sees each job shape-FLIPPED (cpu<->ram swapped, cpu_heavy <->
ram_heavy). A profile trained on this stream predicts a RAM-heavy
future, which is maximally wrong for the CPU-heavy reality. Policies
whose confidence mechanism works should degrade toward the Nomad
baseline instead of collapsing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence, Tuple

from sim.models import Job, NodeState
from sim.workload import generate_cluster, generate_workload, job_resources

ClassMix = Mapping[Tuple[str, str], float]

# --- Cluster configurations ------------------------------------------------

N_NODES = 30

CLUSTERS: Dict[str, dict] = {
    # Homogeneous: every node identical (uniform draw over a zero-width range).
    "homog": dict(cpu_range=(96.0, 96.0), ram_range=(160.0, 160.0)),
    # Heterogeneous: the repository's existing default capacity ranges.
    "hetero": dict(cpu_range=(64.0, 128.0), ram_range=(64.0, 256.0)),
}


def make_cluster(cluster_config: str, seed: int) -> Tuple[NodeState, ...]:
    if cluster_config not in CLUSTERS:
        raise ValueError(f"unknown cluster config {cluster_config!r}")
    kw = CLUSTERS[cluster_config]
    return generate_cluster(N_NODES, seed=seed, **kw)


# --- Class mixes -----------------------------------------------------------

_SIZE_SPREAD = {"small": 0.25, "medium": 0.5, "large": 0.25}


def _mix(shape_weights: Mapping[str, float], size_weights: Mapping[str, float] = _SIZE_SPREAD) -> ClassMix:
    return {
        (size, shape): sw * shw
        for size, sw in size_weights.items()
        for shape, shw in shape_weights.items()
    }


MIX_BALANCED = _mix({"balanced": 1.0})
MIX_CPU = _mix({"cpu_heavy": 0.8, "balanced": 0.2})
MIX_RAM = _mix({"ram_heavy": 0.8, "balanced": 0.2})
MIX_BIMODAL = _mix({"cpu_heavy": 0.45, "ram_heavy": 0.45, "balanced": 0.1})
MIX_TINY_LARGE = _mix(
    {"balanced": 0.5, "cpu_heavy": 0.25, "ram_heavy": 0.25},
    size_weights={"small": 0.7, "large": 0.3},
)
MIX_CPU_BIASED = _mix({"cpu_heavy": 0.7, "balanced": 0.3})

FAMILIES: Tuple[str, ...] = (
    "balanced",      # W1
    "cpu_heavy",     # W2
    "ram_heavy",     # W3
    "bimodal",       # W4
    "tiny_large",    # W5
    "drift",         # W6: first half MIX_CPU, second half MIX_RAM
    "pred_error",    # W7: MIX_CPU_BIASED jobs, flipped observations
    "adversarial",   # W8: MIX_BIMODAL, constructed ordering
)

LOADS: Dict[str, float] = {"low": 0.4, "med": 0.75, "high": 1.1}


# --- Count derivation ------------------------------------------------------


def _mean_demand(mix: ClassMix) -> Tuple[float, float]:
    wsum = sum(mix.values())
    m_cpu = sum(w * job_resources(size, shape)[0] for (size, shape), w in mix.items()) / wsum
    m_ram = sum(w * job_resources(size, shape)[1] for (size, shape), w in mix.items()) / wsum
    return m_cpu, m_ram


def _n_jobs(mix: ClassMix, cluster: Sequence[NodeState], rho: float) -> int:
    c_cpu = sum(n.total_cpu for n in cluster)
    c_ram = sum(n.total_ram for n in cluster)
    m_cpu, m_ram = _mean_demand(mix)
    per_job = max(m_cpu / c_cpu, m_ram / c_ram)
    return max(1, int(rho / per_job))


def _counts(mix: ClassMix, n: int) -> Dict[Tuple[str, str], int]:
    """Largest-remainder rounding of `n * weight` so counts sum to n."""
    wsum = sum(mix.values())
    raw = {k: n * w / wsum for k, w in mix.items()}
    counts = {k: int(v) for k, v in raw.items()}
    short = n - sum(counts.values())
    remainders = sorted(raw, key=lambda k: (-(raw[k] - counts[k]), k))
    for k in remainders[:short]:
        counts[k] += 1
    return counts


# --- Observation transform for the prediction-error family ----------------

_FLIP_SHAPE = {"cpu_heavy": "ram_heavy", "ram_heavy": "cpu_heavy", "balanced": "balanced"}


def flip_job(job: Job) -> Job:
    """CPU<->RAM mirrored view of a job (used only as an observation)."""
    return Job(
        job_id=f"{job.job_id}-flipped",
        cpu=job.ram,
        ram=job.cpu,
        shape=_FLIP_SHAPE.get(job.shape, job.shape),
        size_class=job.size_class,
    )


# --- Trace container -------------------------------------------------------


@dataclass(frozen=True)
class Trace:
    """A fully-materialized experiment input.

    `observed` is what the policy's `observe()` sees for each arrival; it
    equals `jobs` except in the prediction-error family. Same length and
    order as `jobs`.
    """

    family: str
    cluster_config: str
    load: str
    seed: int
    cluster: Tuple[NodeState, ...]
    jobs: Tuple[Job, ...]
    observed: Tuple[Job, ...]


def _adversarial_order(jobs: Sequence[Job]) -> Tuple[Job, ...]:
    """Largest-first; within a size band alternate cpu_heavy / ram_heavy,
    then balanced. Deterministic (sorts on canonical fields only)."""
    ordered: List[Job] = []
    for band in ("large", "medium", "small"):
        band_jobs = [j for j in jobs if j.size_class == band]
        cpu = sorted((j for j in band_jobs if j.shape == "cpu_heavy"), key=lambda j: j.job_id)
        ram = sorted((j for j in band_jobs if j.shape == "ram_heavy"), key=lambda j: j.job_id)
        bal = sorted((j for j in band_jobs if j.shape == "balanced"), key=lambda j: j.job_id)
        inter: List[Job] = []
        for a, b in zip(cpu, ram):
            inter.extend((a, b))
        longer = cpu[len(ram):] + ram[len(cpu):]
        ordered.extend(inter + longer + bal)
    assert len(ordered) == len(jobs)
    return tuple(ordered)


def generate_trace(family: str, cluster_config: str, load: str, seed: int) -> Trace:
    """Deterministically build (cluster, jobs, observations) for one cell+seed.

    Derived RNG streams: cluster uses seed*1000+1, workload shuffling uses
    seed*1000+2 (and +3 for the drift second half) so cluster and workload
    randomness never alias.
    """
    if family not in FAMILIES:
        raise ValueError(f"unknown family {family!r}; valid: {FAMILIES}")
    if load not in LOADS:
        raise ValueError(f"unknown load {load!r}; valid: {sorted(LOADS)}")
    rho = LOADS[load]
    cluster = make_cluster(cluster_config, seed=seed * 1000 + 1)

    if family == "drift":
        # Half the total count from each mix; shuffle within each half only.
        n = _n_jobs(MIX_BIMODAL, cluster, rho)  # sizing mix = average of the two phases
        first = generate_workload(_counts(MIX_CPU, n // 2), seed=seed * 1000 + 2)
        second = generate_workload(_counts(MIX_RAM, n - n // 2), seed=seed * 1000 + 3)
        # Re-id second half to keep job_ids unique across the concatenation.
        second = tuple(
            Job(f"p2-{j.job_id}", j.cpu, j.ram, j.shape, j.size_class) for j in second
        )
        jobs = first + second
        return Trace(family, cluster_config, load, seed, cluster, jobs, jobs)

    mix = {
        "balanced": MIX_BALANCED,
        "cpu_heavy": MIX_CPU,
        "ram_heavy": MIX_RAM,
        "bimodal": MIX_BIMODAL,
        "tiny_large": MIX_TINY_LARGE,
        "pred_error": MIX_CPU_BIASED,
        "adversarial": MIX_BIMODAL,
    }[family]
    n = _n_jobs(mix, cluster, rho)
    shuffle = family != "adversarial"
    jobs = generate_workload(_counts(mix, n), seed=seed * 1000 + 2, shuffle=shuffle)
    if family == "adversarial":
        jobs = _adversarial_order(jobs)

    observed = tuple(flip_job(j) for j in jobs) if family == "pred_error" else jobs
    return Trace(family, cluster_config, load, seed, cluster, jobs, observed)
