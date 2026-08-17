"""A minimal greedy scheduler that applies a Phase One policy across a
cluster of nodes.

Design mirrors the contract documented in sim/policies.py:

    current state -> feasibility check -> hypothetical post-placement state -> policy score

For each job: filter nodes where the job fits, score every feasible node's
*hypothetical* post-placement state with the given policy, and place on the
highest-scoring node. If no node is feasible, the job fails to place and
the cluster is returned unchanged.

This intentionally does not implement Nomad's full ranking pipeline
(constraints, affinity, spread stanzas, candidate sampling) -- it isolates
the fit-scoring behavior from Phase One.

TIE-BREAKING (METHODOLOGY CORRECTION, see METHODOLOGY_CORRECTION_REPORT.md):
the previous version broke ties by sorting on `node_id` string. That is
not independent of cluster structure: `sim.workload.generate_cluster`
assigns `node_id` as `f"node-{i:03d}"` in the exact same loop iteration
that draws each node's random capacity, so `node_id` order *is* node
creation order by construction -- lexical node_id sort and "first node
created" sort are the same sort. Picking the lexically-smallest node_id is
therefore not an arbitrary, naming-independent tiebreak; it silently
depends on how the cluster was generated. This version instead breaks ties
by explicit *input sequence position*: among exactly-tied candidates, the
node that appears earliest in the `cluster` sequence passed into `place`
wins, regardless of what its `node_id` string is. Renaming node IDs cannot
change the outcome; only reordering the `cluster` sequence can. Ties are
defined by exact float equality -- every candidate score is computed by
the same deterministic pipeline from the same policy, so there is no
accumulated numerical drift to justify a tolerance.
"""

from __future__ import annotations

from dataclasses import replace
from typing import NamedTuple, Optional, Sequence, Tuple

from sim.models import Job, NodeState
from sim.policies import Policy


class PlacementResult(NamedTuple):
    job: Job
    node_id: Optional[str]
    score: Optional[float]
    feasible_node_count: int
    tied_candidate_count: int  # how many feasible nodes shared the winning score (1 = no tie, 0 = no placement)

    @property
    def placed(self) -> bool:
        return self.node_id is not None

    @property
    def was_tie(self) -> bool:
        return self.tied_candidate_count > 1


def fits(node: NodeState, job: Job) -> bool:
    """True if `job` can be placed on `node` without exceeding capacity."""
    free_cpu = node.total_cpu - node.used_cpu
    free_ram = node.total_ram - node.used_ram
    return job.cpu <= free_cpu and job.ram <= free_ram


def hypothetical_placement(node: NodeState, job: Job) -> NodeState:
    """Return a *new* NodeState reflecting `job` placed on `node`.

    `node` is never mutated -- NodeState is frozen.
    """
    return replace(node, used_cpu=node.used_cpu + job.cpu, used_ram=node.used_ram + job.ram)


def place(
    cluster: Sequence[NodeState],
    job: Job,
    policy: Policy,
) -> Tuple[PlacementResult, Tuple[NodeState, ...]]:
    """Place a single job onto the best-scoring feasible node.

    Ties (exact score equality) are broken by earliest position in
    `cluster` -- see module docstring. Returns (result, new_cluster). If no
    node is feasible, new_cluster is identical (by value) to the input
    cluster.
    """
    cluster = tuple(cluster)
    feasible = [(idx, n) for idx, n in enumerate(cluster) if fits(n, job)]
    if not feasible:
        return PlacementResult(job, None, None, 0, 0), cluster

    scored = []
    for idx, node in feasible:
        hypothetical = hypothetical_placement(node, job)
        score = policy(hypothetical, job)
        scored.append((score, idx, node))

    best_score = max(s for s, _, _ in scored)
    tied = [t for t in scored if t[0] == best_score]
    tied_count = len(tied)
    # Earliest position in the input sequence wins among ties.
    _, best_idx, best_node = min(tied, key=lambda t: t[1])
    new_best_node = hypothetical_placement(best_node, job)

    new_cluster = tuple(
        new_best_node if i == best_idx else n for i, n in enumerate(cluster)
    )
    result = PlacementResult(job, best_node.node_id, best_score, len(feasible), tied_count)
    return result, new_cluster


def run_schedule(
    cluster: Sequence[NodeState],
    jobs: Sequence[Job],
    policy: Policy,
) -> Tuple[Tuple[NodeState, ...], Tuple[PlacementResult, ...]]:
    """Sequentially schedule `jobs` onto `cluster` using `policy`.

    Returns the final cluster state and the per-job placement results, in
    job order. Jobs are never reordered or retried. `cluster` is not
    mutated; each call starts from the given tuple's values.
    """
    current = tuple(cluster)
    results = []
    for job in jobs:
        result, current = place(current, job, policy)
        results.append(result)
    return current, tuple(results)
