# Phase 4 temporal simulator design

Phase 4 adds a deterministic event-driven lifecycle beside the existing
Phase 1–3 sequential simulator. It does not change the old `Job`, placement,
policy, or static experiment APIs. A `TemporalJob` wraps the existing immutable
job with an arrival time, positive duration, stable arrival order, and optional
policy-observation view.

## Lifecycle and event order

At every timestamp the runner performs exactly these operations:

1. Complete all jobs ending at that time and release their CPU and RAM from
   the exact node on which they started.
2. Register simultaneous arrivals in `(arrival_order, job_id)` order. A job
   that cannot fit on any node at the fully drained baseline is marked
   permanently infeasible; other jobs enter the waiting queue.
3. Scan the queue in arrival order and place every job that fits now through
   the existing full-scan policy interface.

The primary queue discipline is named `fifo_scan_backfill`: an older blocked
job stays queued, but it does not stop a later currently fitting job. This is
not strict head-of-line FIFO. Completion events use a heap ordered by
`(completion_time, start_order, job_id)`. There is no preemption or migration.

The observation horizon includes events at the horizon timestamp. The runner
then records completed, running, queued, and permanently infeasible counts.
Primary experiments stop arrivals and drain all feasible jobs after that
snapshot. Drain data provide uncensored wait and turnaround samples without
pretending horizon-unfinished jobs had completed.

## Workload generation and offered load

Each `(family, cluster, load, seed)` creates one complete immutable trace, which
all four policies replay. Independent deterministic random streams generate
the cluster, Poisson arrival process, resource-class order, durations, and the
second drift phase. Policy execution therefore cannot consume workload
randomness.

Inter-arrival times are exponential. Durations are independent uniforms on
`[8, 12]` simulated time units: positive and bounded so a few accidental
extremes cannot dominate a compact run. Arrivals are observed on `[0, 50]`.
For resource `r`, the target offered load is

```text
rho_r = arrival_rate * E[request_r * duration] / available_cluster_capacity_r
rho   = max(rho_cpu, rho_ram)
```

Tuning seeds 0–9 verified and froze three regions before the held-out run:
underload `rho=0.70`, near saturation `rho=1.00`, and overload `rho=1.30`.
The raw data retain target and realized CPU/RAM load. Held-out seeds are
1000–1009 and are never used for configuration selection.

## Metrics

Horizon throughput is `completed_by_horizon / 50`. The horizon completion
ratio divides completed jobs by feasible jobs. Running and queued work remains
explicit at the horizon.

For a drained job:

```text
waiting_time    = start_time - arrival_time
turnaround_time = completion_time - arrival_time
slowdown        = turnaround_time / duration
```

Mean and P95 wait, turnaround, and slowdown use drained feasible jobs in the
primary study. P95 uses the nearest-rank definition: sort `n` values and select
one-based rank `ceil(0.95*n)`. An empty sample is undefined (`None`), not zero.
Makespan is the last completion time measured from simulation time zero;
drained throughput is `completed_feasible_jobs / makespan`.

CPU utilization, RAM utilization, queue length, and active-node count are
time-weighted areas under their stepwise curves over `[0, 50)`, divided by 50.
The separate `horizon_*` fields are an instantaneous snapshot and must not be
compared as if they were time averages.

## Correctness boundaries

With invariant checks enabled, every event boundary reconciles live allocation
CPU/RAM with node usage and checks capacity bounds. A job can start and complete
at most once, completion must equal start plus duration, completed jobs retain
no live allocation, and a full drain returns every node to its original used
resource baseline. Hand-worked tests cover simultaneous events, waiting,
backfilling, heterogeneous release, horizon censoring, drain behavior,
percentiles, time-weighted metrics, replay, and policy isolation.

The model still covers only CPU and RAM. It does not simulate network, disk,
devices, constraints, priorities, failures, real execution, candidate sampling,
preemption, migration, or autoscaling. Synthetic Poisson arrivals and bounded
durations support controlled mechanism testing, not production-trace validity.
