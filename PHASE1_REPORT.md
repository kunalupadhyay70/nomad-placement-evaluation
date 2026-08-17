# nomad-placement-lab — Phase One Report (redone against the real hashicorp/nomad repo)

## Correction from the prior draft

The earlier version of this report cited `scheduler/rank.go` as the source of Nomad's scoring functions and referenced a project named "nomad-placement-lab" as if it mapped to a GitHub repo. There is no `hashicorp/nomad-placement-lab` repository — `nomad-placement-lab` is just this project's local name. The actual reference implementation lives at **github.com/hashicorp/nomad**, and the scoring functions specifically live in **`nomad/structs/funcs.go`**, not `scheduler/rank.go`. That file was fetched directly and the formulas below are transcribed from it.

## Verified source (nomad/structs/funcs.go)

The original Phase-1 check used the moving `main` branch. The release was
rechecked and pinned to commit
`f3fe893c53d20681232700eb67f89f7478c2fa4e`; see
`docs/NOMAD_FIDELITY.md` for immutable links.

```go
func computeFreePercentage(node *Node, util *ComparableResources) (freePctCpu, freePctRam float64) {
    reserved := node.ReservedResources.Comparable()
    res := node.NodeResources.Comparable()
    nodeCpu := float64(res.Flattened.Cpu.CpuShares)
    nodeMem := float64(res.Flattened.Memory.MemoryMB)
    if reserved != nil {
        nodeCpu -= float64(reserved.Flattened.Cpu.CpuShares)
        nodeMem -= float64(reserved.Flattened.Memory.MemoryMB)
    }
    freePctCpu = 1 - (float64(util.Flattened.Cpu.CpuShares) / nodeCpu)
    freePctRam = 1 - (float64(util.Flattened.Memory.MemoryMB) / nodeMem)
    return freePctCpu, freePctRam
}

// ScoreFitBinPack computes a fit score to achieve pinbacking behavior. Score is in [0, 18]
func ScoreFitBinPack(node *Node, util *ComparableResources) float64 {
    freePctCpu, freePctRam := computeFreePercentage(node, util)
    total := math.Pow(10, freePctCpu) + math.Pow(10, freePctRam)
    score := 20.0 - total
    if score > 18.0 {
        score = 18.0
    } else if score < 0 {
        score = 0
    }
    return score
}

// ScoreFitSpread computes a fit score to achieve spread behavior. Score is in [0, 18]
func ScoreFitSpread(node *Node, util *ComparableResources) float64 {
    freePctCpu, freePctRam := computeFreePercentage(node, util)
    total := math.Pow(10, freePctCpu) + math.Pow(10, freePctRam)
    score := total - 2
    if score > 18.0 {
        score = 18.0
    } else if score < 0 {
        score = 0
    }
    return score
}
```

Both raw scores are bounded to `[0, 18]`. `computeFreePercentage` divides *node resources minus reserved resources* by utilization — the analogue in this project is `NodeState.free_cpu_fraction` / `free_ram_fraction`, computed from `total_cpu`/`total_ram` and `used_cpu`/`used_ram` (reservation is out of scope for Phase One).

## What changed vs. the prior draft

- Citation corrected: `nomad/structs/funcs.go`, not `scheduler/rank.go`.
- The formulas, constants (`20`, `2`, clamp to `[0, 18]`), and normalization (`score / 18` to map to `[0, 1]`) were all already correct and are reused unchanged, now backed by the fetched source instead of a description of it.
- No other implementation changes were needed — the original design (immutable models, post-placement contract, factory-based tunable policy) already matched Nomad's real behavior.

## Project structure

```text
nomad-placement-lab/
├── sim/
│   ├── __init__.py
│   ├── models.py
│   └── policies.py
├── tests/
│   └── test_policies.py
├── phase1_output.json
└── PHASE1_REPORT.md
```

Phase One boundary preserved: no workload generator, scheduler, metrics, experiment runner, plotting code, baseline results, or README.

## Implementation

### Immutable models (`sim/models.py`)

`NodeState` and `Job` are frozen, slotted dataclasses. `NodeState` exposes `free_cpu_fraction` / `free_ram_fraction` as computed properties mirroring `computeFreePercentage`. Validation rejects non-finite values, non-positive node capacity, negative usage/resource values, usage exceeding capacity, and jobs requesting zero of both resources. `frozen=True` makes mutation raise `AttributeError` rather than relying on convention.

### Policies (`sim/policies.py`)

```python
Policy = Callable[[NodeState, Job], float]

binpack(node, job) -> float          # Nomad's ScoreFitBinPack, base 10, normalized to [0, 1]
spread(node, job) -> float           # Nomad's ScoreFitSpread, base 10, normalized to [0, 1]
make_binpack_tunable(base) -> Policy # generalizes binpack to any base > 1; base=10 == binpack exactly
POLICIES = {"binpack": binpack, "spread": spread}
```

Policies receive the node's **post-placement** hypothetical state — job demand is not added inside the policy. That's the scheduler's job in Phase Two:

```text
current state -> feasibility check -> hypothetical post-placement state -> policy score
```

`make_binpack_tunable` rejects `base <= 1` or non-finite bases with `ValueError`.

## Test results

```text
$ PYTHONPATH=. python3 -m pytest tests/ -v
18 passed in 0.06s
```

| Requirement | Tests | Result |
|---|---:|---:|
| Empty node gives `binpack == 0` | 1 | Passed |
| Empty node gives `spread == 1` | 1 | Passed |
| Perfect fit gives `binpack == 1` | 1 | Passed |
| Perfect fit gives `spread == 0` | 1 | Passed |
| `make_binpack_tunable(10)` equals `binpack` exactly | 5 | Passed |
| Balanced 50/50 leftovers outrank 20/80 under bin-pack | 1 | Passed |
| 20/80 leftovers outrank 50/50 under spread | 1 | Passed |
| Scoring leaves node and job unchanged, and mutation raises | 3 | Passed |
| Bases 1, 0, and negative values raise `ValueError` | 4 | Passed |

The base-10 equivalence test uses exact equality (`tunable_base_ten(node, job) == binpack(node, job)`), which holds because both are computed from the same `_raw_total` helper at `base=10.0` — there's no separately-duplicated formula to drift out of sync.

## Fidelity notes

This reproduces the isolated CPU/RAM fit-scoring component only, not Nomad's full scheduler. In real Nomad, feasibility filtering happens before ranking, and affinity/spread stanzas can further adjust scores beyond raw bin-pack/spread fit. Candidate sampling (how many nodes get scored at all) is also out of scope for Phase One and differs between batch and service scheduling in real Nomad. Phase Two — workload generator, scheduler, metrics, experiment runner, plotting — remains unstarted.
