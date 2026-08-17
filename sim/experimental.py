"""Experimental placement policies evaluated against the exact Nomad
baseline in sim/policies.py (which is left untouched).

Score contract (identical to sim/policies.py): every policy is called as
`policy(node, job)` where `node` is the *hypothetical post-placement*
NodeState (the scheduler already added the job's demand -- see
sim/scheduler.py). All scores are in [0, 1]; higher is better. Policies
that need the PRE-placement state reconstruct it by subtracting the job's
demand back out; this is exact, not an approximation.

Policies
--------
B  tetris_alignment      cosine(pre-placement free vector, demand vector)
   balance_aware(lam)    binpack - lam * |free_cpu_frac - free_ram_frac|
   WorkloadMismatchPolicy(lam)
                         binpack - lam * mismatch(leftover shape, EWMA
                         demand shape)   (the "recommended simple
                         heuristic" from the project notes)
C/D  WorkloadAwarePolicy(alpha, beta, gamma, variant)
                         alpha * binpack + beta * tetris
                                          + gamma_eff * future_fit
   With gamma = 0 this is exactly the Hybrid (C) policy; with
   variant="cheap" / "capacity" it is future-fit D1 / D2.

Tetris alignment: PRE-placement free resources vs. demand
---------------------------------------------------------
    free   = [free_cpu / total_cpu,  free_ram / total_ram]   (before job)
    demand = [job_cpu / total_cpu,   job_ram / total_ram]
    score  = cos(free, demand),  0.0 if either norm is 0

Why pre-placement: this is the Tetris formulation (Grandl et al.,
SIGCOMM'14) -- score the match between what the job NEEDS and what the
node HAS, so CPU-heavy jobs steer to CPU-abundant nodes. The
post-placement residual view is deliberately NOT used here because the
usefulness of the residual is exactly what the future-fit term measures;
keeping alignment pre-placement keeps the two components non-redundant.
Both vectors are componentwise >= 0, so cosine is already in [0, 1] and
needs no re-normalization.

Future-fit terms (per-candidate, cluster-constant terms dropped)
----------------------------------------------------------------
For one job, every candidate placement leaves the REST of the cluster in
an identical state -- candidates differ only in one node's residual.
Adding any cluster-wide constant to all candidates cannot change the
argmax, so both variants score only the candidate node's residual:

  cheap    ff1(n) = sum_b p(b) * 1[rep_b fits in residual(n)]
  capacity ff2(n) = sum_b p(b) * min(k_b(n), CAP) / CAP
           k_b(n) = min(floor(free_cpu / cpu_b), floor(free_ram / ram_b))

where p is the EWMA profile distribution, rep_b the representative job of
bucket b, and CAP (default 3) bounds the count so one giant node cannot
saturate the term. Both are in [0, 1].

Confidence fallback
-------------------
    gamma_eff = gamma * profile.confidence
    alpha_eff = alpha + (gamma - gamma_eff)      # excess goes to Nomad

When the profile predicts poorly (high EWMA prediction error, e.g. under
drift or a corrupted signal), confidence -> 0 and the policy degrades
toward alpha' * binpack + beta * tetris; with beta = 0 it becomes the
exact Nomad baseline. A fresh profile starts at confidence 0, so the
workload-aware term must earn trust before it can steer placements.
"""

from __future__ import annotations

import math
from typing import Dict

from sim.models import Job, NodeState
from sim.policies import Policy, binpack
from sim.profile import BUCKET_RESOURCES, WorkloadProfile

_WEIGHT_TOL = 1e-9


# --- B: Tetris-style alignment (stateless) --------------------------------


def tetris_alignment(node: NodeState, job: Job) -> float:
    """Cosine similarity between pre-placement free and job demand vectors.

    `node` is post-placement per the scheduler contract; the pre-placement
    free resources are reconstructed exactly by adding the job's demand
    back. Zero-norm edge cases (a node with no free resources before
    placement can never be a feasible candidate, but scoring must still
    be safe; a job with zero total demand is rejected by the Job model)
    return 0.0.
    """
    pre_used_cpu = node.used_cpu - job.cpu
    pre_used_ram = node.used_ram - job.ram
    free_cpu = (node.total_cpu - pre_used_cpu) / node.total_cpu
    free_ram = (node.total_ram - pre_used_ram) / node.total_ram
    dem_cpu = job.cpu / node.total_cpu
    dem_ram = job.ram / node.total_ram

    free_norm = math.hypot(free_cpu, free_ram)
    dem_norm = math.hypot(dem_cpu, dem_ram)
    if free_norm <= 0.0 or dem_norm <= 0.0:
        return 0.0
    cos = (free_cpu * dem_cpu + free_ram * dem_ram) / (free_norm * dem_norm)
    # Guard float error; both vectors are componentwise >= 0 in practice.
    return max(0.0, min(1.0, cos))


# --- Balance-aware bin-pack (stateless, workload-agnostic) ----------------


def make_balance_aware(lam: float) -> Policy:
    """binpack(node, job) - lam * |free_cpu_fraction - free_ram_fraction|.

    Post-placement free fractions. The penalty term is in [0, 1], so with
    binpack in [0, 1] the result is clamped to [0, 1] to keep the shared
    score contract. lam >= 0 required; lam = 0 is exactly binpack.
    """
    if not math.isfinite(lam) or lam < 0:
        raise ValueError(f"lam must be finite and >= 0, got {lam!r}")

    def policy(node: NodeState, job: Job) -> float:
        imbalance = abs(node.free_cpu_fraction - node.free_ram_fraction)
        return max(0.0, min(1.0, binpack(node, job) - lam * imbalance))

    policy.__name__ = f"balance_aware_lam{lam}"
    return policy


# --- Workload-mismatch penalty (stateful; the simple recommended form) ----


class WorkloadMismatchPolicy:
    """binpack - lam * mismatch(post-placement leftover shape, EWMA demand shape).

        leftover cpu share L_c = fc / (fc + fr)   (capacity-normalized
        free fractions fc, fr; heterogeneity-safe per the project notes)
        demand   cpu share D_c = profile demand_shares()
        mismatch = (|L_c - D_c| + |L_r - D_r|) / 2 = |L_c - D_c|  in [0, 1]

    Optional confidence fallback (on by default): lam_eff = lam *
    profile.confidence, so a poorly-predicting profile shrinks the
    penalty toward exact Nomad behaviour.
    Call `observe(job)` after each arrival decision to update the profile.
    """

    def __init__(
        self,
        lam: float,
        decay: float = 0.1,
        confidence_fallback: bool = True,
    ) -> None:
        if not math.isfinite(lam) or lam < 0:
            raise ValueError(f"lam must be finite and >= 0, got {lam!r}")
        self.lam = lam
        self.confidence_fallback = confidence_fallback
        self.profile = WorkloadProfile(decay=decay)

    def observe(self, job: Job) -> None:
        self.profile.observe(job)

    def __call__(self, node: NodeState, job: Job) -> float:
        fc = node.free_cpu_fraction
        fr = node.free_ram_fraction
        total = fc + fr
        if total <= 0.0:
            mismatch = 0.0  # nothing left over -> nothing to mismatch
        else:
            demand_cpu_share, _ = self.profile.demand_shares()
            mismatch = abs(fc / total - demand_cpu_share)
        lam = self.lam * (self.profile.confidence if self.confidence_fallback else 1.0)
        return max(0.0, min(1.0, binpack(node, job) - lam * mismatch))


# --- C / D: weighted Nomad + alignment + future-fit -----------------------


class WorkloadAwarePolicy:
    """alpha * binpack + beta * tetris + gamma_eff * future_fit.

    Weight contract: alpha, beta, gamma each in [0, 1] and
    alpha + beta + gamma == 1 (tolerance 1e-9), else ValueError.

    gamma == 0  -> Hybrid (C); the profile is never consulted for scoring.
    variant     -> "cheap" (D1) or "capacity" (D2); see module docstring.
    Confidence fallback shifts unearned gamma mass onto alpha (Nomad).
    Call `observe(job)` after each arrival decision to update the profile.
    """

    def __init__(
        self,
        alpha: float,
        beta: float,
        gamma: float,
        variant: str = "cheap",
        decay: float = 0.1,
        capacity_cap: int = 3,
        confidence_fallback: bool = True,
    ) -> None:
        for name, w in (("alpha", alpha), ("beta", beta), ("gamma", gamma)):
            if not math.isfinite(w) or not (0.0 <= w <= 1.0):
                raise ValueError(f"{name} must be in [0, 1], got {w!r}")
        if abs(alpha + beta + gamma - 1.0) > _WEIGHT_TOL:
            raise ValueError(
                f"alpha + beta + gamma must equal 1, got {alpha + beta + gamma!r}"
            )
        if variant not in ("cheap", "capacity"):
            raise ValueError(f"variant must be 'cheap' or 'capacity', got {variant!r}")
        if capacity_cap < 1:
            raise ValueError(f"capacity_cap must be >= 1, got {capacity_cap!r}")
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.variant = variant
        self.capacity_cap = capacity_cap
        self.confidence_fallback = confidence_fallback
        self.profile = WorkloadProfile(decay=decay)

    def observe(self, job: Job) -> None:
        self.profile.observe(job)

    # Future-fit on the candidate node's post-placement residual only:
    # cluster-constant terms are dropped (cannot change the argmax; see
    # module docstring).
    def _future_fit(self, node: NodeState) -> float:
        free_cpu = node.total_cpu - node.used_cpu
        free_ram = node.total_ram - node.used_ram
        score = 0.0
        if self.variant == "cheap":
            for b, prob in self.profile.p.items():
                cpu, ram = BUCKET_RESOURCES[b]
                if cpu <= free_cpu and ram <= free_ram:
                    score += prob
        else:  # capacity
            cap = float(self.capacity_cap)
            for b, prob in self.profile.p.items():
                cpu, ram = BUCKET_RESOURCES[b]
                k = min(free_cpu // cpu if cpu > 0 else cap, free_ram // ram if ram > 0 else cap)
                score += prob * (min(k, cap) / cap)
        return score

    def __call__(self, node: NodeState, job: Job) -> float:
        if self.gamma > 0.0 and self.confidence_fallback:
            gamma_eff = self.gamma * self.profile.confidence
        else:
            gamma_eff = self.gamma
        alpha_eff = self.alpha + (self.gamma - gamma_eff)

        score = alpha_eff * binpack(node, job)
        if self.beta > 0.0:
            score += self.beta * tetris_alignment(node, job)
        if gamma_eff > 0.0:
            score += gamma_eff * self._future_fit(node)
        return score


def describe(policy: object) -> Dict[str, object]:
    """Small introspection helper for experiment logging."""
    if isinstance(policy, WorkloadAwarePolicy):
        return {
            "alpha": policy.alpha,
            "beta": policy.beta,
            "gamma": policy.gamma,
            "variant": policy.variant,
            "decay": policy.profile.decay,
            "fallback": policy.confidence_fallback,
        }
    if isinstance(policy, WorkloadMismatchPolicy):
        return {
            "lam": policy.lam,
            "decay": policy.profile.decay,
            "fallback": policy.confidence_fallback,
        }
    return {"name": getattr(policy, "__name__", type(policy).__name__)}
