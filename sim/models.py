"""Immutable data models for the Nomad-inspired placement simulation.

These models mirror the resource shapes used by HashiCorp Nomad's real
scheduler closely enough to reproduce its bin-pack / spread scoring
behavior, without importing Nomad itself.

Verified against: github.com/hashicorp/nomad, nomad/structs/funcs.go
(computeFreePercentage), commit f3fe893c53d20681232700eb67f89f7478c2fa4e.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NodeState:
    """A node's resource capacity and (hypothetical) usage.

    `used_cpu` / `used_ram` are expected to already reflect any hypothetical
    placement under consideration -- this model does not add job demand to
    usage itself. See sim/policies.py for the post-placement contract.
    """

    node_id: str
    total_cpu: float
    total_ram: float
    used_cpu: float = 0.0
    used_ram: float = 0.0

    def __post_init__(self) -> None:
        for name, value in (
            ("total_cpu", self.total_cpu),
            ("total_ram", self.total_ram),
            ("used_cpu", self.used_cpu),
            ("used_ram", self.used_ram),
        ):
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite, got {value!r}")

        if self.total_cpu <= 0:
            raise ValueError(f"total_cpu must be positive, got {self.total_cpu!r}")
        if self.total_ram <= 0:
            raise ValueError(f"total_ram must be positive, got {self.total_ram!r}")
        if self.used_cpu < 0:
            raise ValueError(f"used_cpu must be >= 0, got {self.used_cpu!r}")
        if self.used_ram < 0:
            raise ValueError(f"used_ram must be >= 0, got {self.used_ram!r}")
        if self.used_cpu > self.total_cpu:
            raise ValueError("used_cpu cannot exceed total_cpu")
        if self.used_ram > self.total_ram:
            raise ValueError("used_ram cannot exceed total_ram")

    @property
    def free_cpu_fraction(self) -> float:
        """Fraction of CPU free, mirroring Nomad's computeFreePercentage."""
        return 1.0 - (self.used_cpu / self.total_cpu)

    @property
    def free_ram_fraction(self) -> float:
        """Fraction of RAM free, mirroring Nomad's computeFreePercentage."""
        return 1.0 - (self.used_ram / self.total_ram)


@dataclass(frozen=True, slots=True)
class Job:
    """A schedulable unit of work requesting CPU and RAM.

    `shape` and `size_class` are independent, optional descriptive labels:
    `shape` names the CPU:RAM ratio (e.g. "balanced", "cpu_heavy"), and
    `size_class` names the total resource magnitude (e.g. "small",
    "medium"). Neither is validated here -- callers that need canonical,
    validated size/shape combinations should use
    `sim.workload.generate_workload`, which is the source of truth for the
    canonical size/shape tables. Both default to "unspecified" so ad hoc
    jobs (e.g. in tests) don't have to supply values that don't apply to
    them.
    """

    job_id: str
    cpu: float
    ram: float
    shape: str = "unspecified"
    size_class: str = "unspecified"

    def __post_init__(self) -> None:
        for name, value in (("cpu", self.cpu), ("ram", self.ram)):
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite, got {value!r}")
            if value < 0:
                raise ValueError(f"{name} must be >= 0, got {value!r}")
        if self.cpu == 0 and self.ram == 0:
            raise ValueError("Job must request at least one non-zero resource")
