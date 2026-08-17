#!/usr/bin/env python3
"""Run a small deterministic Phase-4 lifecycle smoke study.

The smoke is intentionally not a headline experiment. It exercises the full
arrival -> queue -> placement -> completion -> release -> drain pipeline with
invariant checks enabled and writes a byte-stable JSON artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.temporal import QUEUE_DISCIPLINE, JobTimeline, run_temporal
from sim.temporal_matrix import primary_temporal_configs
from sim.temporal_workload import generate_temporal_trace

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "results" / "temporal" / "smoke.json"

SMOKE_CONFIG = {
    "family": "bimodal",
    "cluster_config": "hetero",
    "load": "med",
    "seed": 42,
    "observation_horizon": 12.0,
}


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _timeline_to_json(item: JobTimeline) -> dict[str, object]:
    # The full immutable request appears once in ``trace``; ledger rows only
    # need identity and lifecycle fields for reconciliation.
    return {
        "job_id": item.job.job_id,
        "queue_entry_time": item.queue_entry_time,
        "start_time": item.start_time,
        "completion_time": item.completion_time,
        "node_id": item.node_id,
        "state_at_horizon": item.state_at_horizon,
        "final_state": item.final_state,
    }


def main() -> None:
    trace = generate_temporal_trace(**SMOKE_CONFIG)
    trace_input = {
        "cluster": [asdict(node) for node in trace.cluster],
        "jobs": [asdict(job) for job in trace.jobs],
    }
    trace_sha256 = hashlib.sha256(_canonical_json(trace_input).encode()).hexdigest()

    policies: dict[str, object] = {}
    for config in primary_temporal_configs():
        result = run_temporal(
            trace,
            config.factory(),
            config.name,
            drain=True,
            check_invariants=True,
        )
        policies[config.name] = {
            "params": config.params,
            "event_count": result.event_count,
            "metrics": asdict(result.metrics),
            "ledger": [_timeline_to_json(item) for item in result.ledger],
            "horizon_cluster": [asdict(node) for node in result.horizon_cluster],
            "final_cluster": [asdict(node) for node in result.final_cluster],
        }

    output = {
        "phase": 4,
        "experiment_type": "deterministic_temporal_smoke",
        "claim_scope": "pipeline validation only; not a general policy comparison",
        "queue_discipline": QUEUE_DISCIPLINE,
        "drain_after_horizon": True,
        "config": SMOKE_CONFIG,
        "trace_sha256": trace_sha256,
        "trace": trace_input,
        "policies": policies,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(f"wrote {OUTPUT}")
    print(f"trace_jobs={len(trace.jobs)} trace_sha256={trace_sha256}")


if __name__ == "__main__":
    main()
