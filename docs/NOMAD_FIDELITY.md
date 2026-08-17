# Nomad fidelity boundary

This project reproduces one isolated scoring component, not the complete
HashiCorp Nomad scheduler.

## Pinned upstream reference

The source was rechecked on 2026-08-18 at HashiCorp Nomad commit
[`f3fe893c53d20681232700eb67f89f7478c2fa4e`](https://github.com/hashicorp/nomad/commit/f3fe893c53d20681232700eb67f89f7478c2fa4e):

- [`computeFreePercentage`, `ScoreFitBinPack`, and `ScoreFitSpread`](https://github.com/hashicorp/nomad/blob/f3fe893c53d20681232700eb67f89f7478c2fa4e/nomad/structs/funcs.go#L234)
  in `nomad/structs/funcs.go`
- [`binPackingMaxFitScore = 18.0`](https://github.com/hashicorp/nomad/blob/f3fe893c53d20681232700eb67f89f7478c2fa4e/scheduler/feasible/rank.go#L21)
  and fit-score normalization in `scheduler/feasible/rank.go`

Pinning the commit makes the comparison immutable; a moving `main` branch is
not used as release evidence.

## Reproduced behavior

For a hypothetical post-placement node state, the simulator computes:

```text
free_cpu = 1 - used_cpu / total_cpu
free_ram = 1 - used_ram / total_ram
total = 10^free_cpu + 10^free_ram

binpack = clamp(20 - total, 0, 18) / 18
spread  = clamp(total - 2, 0, 18) / 18
```

The division by 18 only normalizes Nomad's raw `[0, 18]` fit score to the
simulator's shared `[0, 1]` policy interface. It does not change candidate
ordering. The scheduler filters infeasible nodes before scoring, passes each
policy a hypothetical post-placement state, and retains the first encountered
candidate at the maximum score.

## Deliberate simplifications

- Reserved resources are modeled as zero, so `total_cpu` and `total_ram` are
  already the capacities available to the simulator.
- Every feasible node is scored. This is full-scan greedy placement, not
  Nomad's bounded candidate evaluation and not an oracle or global optimum.
- Only CPU and RAM participate in feasibility and scoring.
- The simulator omits Nomad constraints, affinity and anti-affinity, spread
  stanzas, devices, networking, storage, preemption, concurrent evaluations,
  rescheduling, and the rest of the production ranking pipeline.
- Jobs never complete or release resources; this is a sequential online
  admission experiment, not a production scheduler or discrete-event model.

The fidelity claim is therefore narrow: the two CPU/RAM fit-score formulas and
their post-placement scoring contract are reproduced; broader Nomad behavior is
not claimed.

