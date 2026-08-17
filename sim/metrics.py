"""Cluster-level metrics for evaluating placement policies.

None of these are Nomad APIs -- they're simple, well-defined measures for
comparing binpack vs. spread (vs. tunable) behavior in this simulation.

METHODOLOGY CORRECTION (see METHODOLOGY_CORRECTION_REPORT.md): the
previous version of this module called the metric below "fragmentation".
That name overclaimed: the formula measures how *dispersed* residual free
capacity is across nodes, not whether that residual capacity is usable by
future jobs, and not fragmentation in the general sense (e.g. it says
nothing about whether free capacity is split awkwardly between CPU and
RAM on the same node). It has been renamed to `cpu_dispersion` /
`ram_dispersion`, and this module now also reports operational
schedulability metrics (`nodes_fitting_class`, `additional_placeable`)
that answer a more concrete question: can specific future jobs actually
fit in what's left?

- **utilization**: mean fraction of capacity used, per resource dimension.
- **load imbalance** (`cpu_imbalance` / `ram_imbalance`): population stdev
  of per-node utilization. Higher means load is concentrated on fewer
  nodes; lower means it's spread evenly. This is about *used* capacity.
- **residual free-fraction balance gap** (`cpu_free_fraction_gap` /
  `ram_free_fraction_gap`): the difference between the most-free and
  least-free node's free-capacity fraction. `0` means every node has the
  same free fraction; larger values mean some nodes are far freer than
  others. This is about the *spread* of free capacity, not its
  concentration on a single node -- see dispersion below for that.
- **residual-capacity dispersion** (`cpu_dispersion` / `ram_dispersion`):
      D_r = 1 - (max_i free_r(node_i) / sum_i free_r(node_i))
  For one resource `r`. Range is `[0, 1]` when total free capacity > 0.
  - `D_r -> 0` means nearly all free capacity for that resource sits on a
    single node (residual capacity is *concentrated*).
  - `D_r -> 1` (approaching `1 - 1/N` for N nodes with exactly equal free
    capacity) means free capacity is spread thinly across many nodes
    (residual capacity is *dispersed*).
  - If total free capacity for that resource is exactly `0` (cluster is
    completely full on that resource), `D_r` is defined as `0.0` by
    convention: there is no free capacity to disperse, so "concentrated
    on one node" (vacuously) is the more defensible reading than "maximally
    dispersed". This is a documented convention, not a claim that a full
    cluster is well-packed.
  - A **higher** dispersion value is *not* inherently worse or better --
    it says nothing by itself about whether a specific future job can be
    placed. Use the schedulability metrics below for that question.
- **operational schedulability**: for each canonical (size_class, shape)
  job class, how many nodes currently have enough free CPU *and* RAM to
  fit one more such job. This directly answers "can this cluster still
  take a job like X", which dispersion and imbalance do not.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Dict, Mapping, Sequence, Tuple

from sim.models import NodeState


@dataclass(frozen=True)
class ClusterMetrics:
    node_count: int
    active_nodes: int
    idle_nodes: int
    mean_cpu_utilization: float
    mean_ram_utilization: float
    cpu_imbalance: float
    ram_imbalance: float
    cpu_free_fraction_gap: float
    ram_free_fraction_gap: float
    cpu_dispersion: float
    ram_dispersion: float


def _dispersion(free_values: Sequence[float]) -> float:
    total_free = sum(free_values)
    if total_free <= 0:
        # Documented convention: no free capacity means nothing to
        # disperse. See module docstring.
        return 0.0
    return 1.0 - (max(free_values) / total_free)


def compute_metrics(cluster: Sequence[NodeState]) -> ClusterMetrics:
    if not cluster:
        raise ValueError("cluster must contain at least one node")

    cpu_utils = [n.used_cpu / n.total_cpu for n in cluster]
    ram_utils = [n.used_ram / n.total_ram for n in cluster]

    active = sum(1 for n in cluster if n.used_cpu > 0 or n.used_ram > 0)

    free_cpu = [n.total_cpu - n.used_cpu for n in cluster]
    free_ram = [n.total_ram - n.used_ram for n in cluster]

    free_cpu_fractions = [n.free_cpu_fraction for n in cluster]
    free_ram_fractions = [n.free_ram_fraction for n in cluster]

    return ClusterMetrics(
        node_count=len(cluster),
        active_nodes=active,
        idle_nodes=len(cluster) - active,
        mean_cpu_utilization=statistics.fmean(cpu_utils),
        mean_ram_utilization=statistics.fmean(ram_utils),
        cpu_imbalance=statistics.pstdev(cpu_utils),
        ram_imbalance=statistics.pstdev(ram_utils),
        cpu_free_fraction_gap=max(free_cpu_fractions) - min(free_cpu_fractions),
        ram_free_fraction_gap=max(free_ram_fractions) - min(free_ram_fractions),
        cpu_dispersion=_dispersion(free_cpu),
        ram_dispersion=_dispersion(free_ram),
    )


# --- Operational schedulability -------------------------------------------


def nodes_fitting(cluster: Sequence[NodeState], cpu_demand: float, ram_demand: float) -> int:
    """Count nodes in `cluster` that currently have enough free CPU *and*
    RAM to fit one job requesting (cpu_demand, ram_demand)."""
    count = 0
    for n in cluster:
        free_cpu = n.total_cpu - n.used_cpu
        free_ram = n.total_ram - n.used_ram
        if cpu_demand <= free_cpu and ram_demand <= free_ram:
            count += 1
    return count


def compute_class_fit_counts(
    cluster: Sequence[NodeState],
    class_resources: Mapping[str, Tuple[float, float]],
) -> Dict[str, int]:
    """For each `class_key -> (cpu_demand, ram_demand)` entry, count how
    many nodes in `cluster` could fit one more job of that class right now.

    This is a per-node capacity check only -- it does not simulate actually
    placing a job, and does not account for interactions between multiple
    hypothetical future jobs. See `sim.experiment` for a greedy repeated-
    placement estimate (`additional_jobs_placeable_<class>`) that does
    simulate sequential placement, if a less conservative estimate is
    needed.
    """
    return {
        name: nodes_fitting(cluster, cpu, ram)
        for name, (cpu, ram) in class_resources.items()
    }
