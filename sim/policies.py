"""Placement scoring policies mirroring HashiCorp Nomad's bin-pack / spread
scoring.

Verified against the real hashicorp/nomad GitHub repository at commit
f3fe893c53d20681232700eb67f89f7478c2fa4e, nomad/structs/funcs.go
(ScoreFitBinPack, ScoreFitSpread):

    freePctCpu, freePctRam = computeFreePercentage(node, util)
    total := math.Pow(10, freePctCpu) + math.Pow(10, freePctRam)

    // ScoreFitBinPack
    score := 20.0 - total          // clamped to [0, 18]

    // ScoreFitSpread
    score := total - 2             // clamped to [0, 18]

Both raw scores are bounded to [0, 18] in Nomad. This module normalizes to
[0, 1] by dividing by 18, and generalizes the fixed base 10 to an arbitrary
base > 1 via `make_binpack_tunable`. At base 10, the tunable policy is
exactly equal to `binpack`.

All policies receive the node's *post-placement* hypothetical state --
this module does not add the job's demand to the node itself. That is the
scheduler's responsibility (Phase Two).
"""

from __future__ import annotations

import math
from typing import Callable, Dict

from sim.models import Job, NodeState

Policy = Callable[[NodeState, Job], float]


def _raw_total(node: NodeState, base: float) -> float:
    return base ** node.free_cpu_fraction + base ** node.free_ram_fraction


def binpack(node: NodeState, job: Job) -> float:
    """Nomad's bin-pack policy at base 10, normalized to [0, 1].

    Higher score = tighter fit (less free space) = higher density.
    """
    total = _raw_total(node, 10.0)
    raw = 20.0 - total
    raw = max(0.0, min(18.0, raw))
    return raw / 18.0


def spread(node: NodeState, job: Job) -> float:
    """Nomad's spread policy at base 10, normalized to [0, 1].

    Higher score = more free space = more even load distribution.
    """
    total = _raw_total(node, 10.0)
    raw = total - 2.0
    raw = max(0.0, min(18.0, raw))
    return raw / 18.0


def make_binpack_tunable(base: float) -> Policy:
    """Build a bin-pack policy generalized to an arbitrary exponential base.

    At base=10 this is exactly equal to `binpack`. `base` must be finite
    and strictly greater than one.
    """
    if not math.isfinite(base):
        raise ValueError(f"base must be finite, got {base!r}")
    if not base > 1:
        raise ValueError(f"base must be > 1, got {base!r}")

    ceiling = 2.0 * base
    span = ceiling - 2.0

    def policy(node: NodeState, job: Job) -> float:
        total = _raw_total(node, base)
        raw = ceiling - total
        raw = max(0.0, min(span, raw))
        return raw / span

    return policy


POLICIES: Dict[str, Policy] = {
    "binpack": binpack,
    "spread": spread,
}
