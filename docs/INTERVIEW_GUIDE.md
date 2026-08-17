# Interview guide

Use the first sentence of each answer for a recruiter or generalist. Continue
with the technical detail only when the interviewer asks.

## Thirty-second explanation

I built a deterministic event-driven CPU/RAM scheduler simulator around a
pinned HashiCorp Nomad fit score. Jobs arrive, wait, run, complete, and release
their exact allocations. In 1,920 paired held-out temporal runs, Tetris improved
overload throughput by 1.38% but used 1.19 more active nodes and increased P95
wait; at low load it added no throughput, and near saturation it was slightly
worse. The honest result is a load-dependent trade-off, not a universal win.

## Basics

### What problem does Nomad solve?

Nomad places and runs workloads across a cluster of machines. A production
scheduler must first decide which nodes are feasible, then rank feasible
choices while respecting many concerns such as constraints, affinities,
devices, networking, and failure recovery.

This project isolates only the CPU/RAM fit-score portion of that much larger
pipeline.

### What is cluster scheduling?

It is the repeated decision of which machine should receive each workload.
The decision must respect capacity and usually optimize competing goals such
as admission, consolidation, balance, locality, and reliability.

### What is bin packing?

Bin packing tries to place items tightly into a small number of bins. Here the
items request two resources—CPU and RAM—and nodes are two-dimensional bins.
Packing tightly can leave whole nodes idle, which is good for consolidation,
but a locally tight choice can leave awkward CPU/RAM shapes for later jobs.

### What is fragmentation?

Fragmentation means total free capacity exists but is split into shapes or
locations that cannot satisfy a request. In two resources, a cluster may have
enough free CPU and enough free RAM in aggregate while no single node has both.

The project avoids using one vague “fragmentation score” as proof. It reports
direct admission outcomes, per-class fit counts, a narrowly named residual
dispersion metric, and a stranded-capacity measure with explicit definitions.

### What is admission rate?

Admission rate is `requests placed / requests submitted`. A rejected request
fits on no candidate under the current residual cluster state.

That remains the Phase 3 metric. Phase 4 separately adds arrival times,
durations, completion/release, a waiting queue, horizon throughput, and drained
latency. The two experiments are not relabelled or pooled.

## What I implemented

### What exactly did you build?

I built two compatible modes: the original online sequential admission
simulator and an additive event-driven lifecycle runner. Both use immutable
node/job models, feasibility filtering, pluggable scoring, seeded immutable
traces, paired statistics, validated CSV artifacts, and reproducible plots.
The temporal mode adds completion ordering, exact release, FIFO-scan
backfilling, a job ledger, horizon snapshots, drain mode, and time-weighted
metrics.

### Did you modify Nomad itself?

No. I reproduced two formulas from a pinned Nomad source revision and studied
them in isolation. This is a Python simulator, not a Nomad fork, plugin, or
production integration.

### Why build a simulator?

It isolates one mechanism and makes policy comparisons controlled and cheap.
Every policy sees the same nodes, requests, order, and seed, so outcome
differences can be attributed to placement decisions rather than an entire
distributed system.

The cost is external validity: a controlled simulator does not establish what
will happen in production Nomad.

### What part of Nomad did you reproduce?

The CPU/RAM free-fraction calculation, binpack/spread formulas, `[0, 18]`
clamping, normalization, and post-placement scoring contract. The source is
pinned in `docs/NOMAD_FIDELITY.md`.

### Why CPU and RAM?

They are the two resources used by the isolated Nomad fit formula, and they
create the core shape problem: CPU-heavy, RAM-heavy, and balanced requests can
leave very different residuals.

### Why synthetic workloads?

Synthetic families let me vary size, shape, order, cluster heterogeneity, and
offered load independently. That supports mechanism testing. They are not
claimed to represent every production trace; trace-driven replay is a logical
next validation step.

### How does the temporal event loop work?

At the same timestamp, I complete and release running jobs first, register
arrivals in stable order second, and dispatch the queue third. Completion-first
means a new arrival can use resources released at that exact time. Completion
events use completion time, start order, and job ID as stable keys, so results
do not depend on dictionary ordering.

The queue discipline is FIFO-order scan with backfilling. I consider jobs in
arrival order, leave currently blocked jobs waiting, and allow a later job to
start if it fits. It avoids strict head-of-line blocking but can increase tail
wait for older awkward jobs. There is no preemption or migration.

### How do you know resource release is correct?

Every live allocation records its original node and exact CPU/RAM demand.
Completion subtracts those values from that node. At each checked event
boundary, the runner reconciles live allocations against node usage and checks
capacity bounds. A full drain must return the cluster to its initial
used-resource baseline. Hand-worked tests cover heterogeneous nodes and
simultaneous completions.

### What is temporal offered load?

I derive arrival rate from resource-time demand, not a job-count label:

```text
rho = max(lambda * E[cpu * duration] / total_cpu,
          lambda * E[ram * duration] / total_ram)
```

Tuning seeds froze `rho=0.70`, `1.00`, and `1.30` as underload, saturation,
and overload before held-out execution. Arrivals are Poisson and durations are
bounded `Uniform[8,12]` so accidental extreme durations cannot dominate.

### How are unfinished jobs handled?

At time 50 I report completed, running, queued, and permanently infeasible
counts without pretending unfinished jobs completed. Then I stop arrivals and
optionally drain all feasible work. Horizon throughput uses completions by time
50; wait, turnaround, slowdown, and makespan use the drained ledger so those
samples are uncensored. P95 is deterministic nearest-rank.

## Algorithms

### Explain Nomad's scoring intuition.

For each feasible node, score the hypothetical state after placing the job.
Let `fc` and `fr` be post-placement free CPU and RAM fractions:

```text
total   = 10^fc + 10^fr
binpack = clamp(20 - total, 0, 18) / 18
spread  = clamp(total - 2, 0, 18) / 18
```

Binpack prefers less remaining capacity; spread prefers more. The exponential
also makes the score sensitive to CPU/RAM balance, so it is inaccurate to call
Nomad's score fragmentation-blind.

### Why exponential scoring?

The base-10 exponential makes large free fractions dominate the sum. Under
binpack, subtracting that sum rewards tight post-placement states and, for a
fixed amount of free capacity, generally favors more balanced CPU/RAM
residuals. I reproduced this behavior rather than choosing the formula.

### What does Tetris placement mean here?

It matches the shape of the arriving request with the shape of a node's
pre-placement free resources:

```text
free   = [free_cpu / total_cpu, free_ram / total_ram]
demand = [job_cpu / total_cpu, job_ram / total_ram]
score  = cosine(free, demand)
```

A CPU-heavy job therefore prefers a node that is relatively CPU-abundant; a
RAM-heavy job prefers a RAM-abundant node. Capacity normalization lets the
comparison work across heterogeneous node sizes.

### Why can shape matching help?

It consumes the resource a node has in relative abundance instead of turning
many nodes into the same awkward residual shape. In mixed workloads, later
requests are more likely to find a node with both resources they need.

That is a mechanism hypothesis supported by the synthetic study, not a proof
that Tetris is globally optimal.

### Why can Tetris lose?

It does not directly price consolidation. It often activates a new node to get
a better shape match, while binpack keeps filling an already active node. In a
uniformly CPU-heavy workload there is little complementary shape diversity to
preserve, so alignment can forfeit packing quality without gaining future fit.

### What is the hybrid?

The selected hybrid is `0.1 × binpack + 0.9 × Tetris`. Its weight was chosen
only on tuning seeds. It tracks Tetris admission closely while using about one
fewer active node on average, but it introduces a tuned parameter; pure Tetris
is the simpler zero-tunable story.

### What is the computational complexity?

For `N` nodes and `M` sequential requests, stateless full-scan policies take
`O(MN)` scoring work. Tetris and Nomad scoring are constant-time per candidate.
Future-fit examines nine fixed workload buckets per candidate, so it is
`O(MNB)` with `B = 9`, still `O(MN)` for this fixed class set but with a larger
constant. Cluster-state reconstruction is also linear in `N` per accepted job.

### What happens with a bounded candidate set?

The winner may change because some feasible nodes are never scored, so
traversal order and candidate budget become part of the experiment. Infeasible
nodes should be skipped rather than consuming a feasible-candidate budget.

This simulator deliberately scores every feasible node. That is full-scan
greedy placement—not an oracle, not globally optimal, and not a reproduction of
Nomad's candidate sampling.

## Experiments

### How did you guarantee fairness?

Each `(family, cluster, load, seed)` creates one immutable `Trace`. Every policy
starts from the same cluster and receives the identical request sequence and
arrival order. The Phase 4 trace also fixes arrivals and durations. Stateful
policies get a fresh instance for every run. Tests verify determinism, shared
trace metadata, and no cross-policy mutation.

### Why multiple seeds?

Cluster capacity draws and request order affect greedy placement. Ten held-out
seeds show whether a difference repeats rather than depending on one favorable
ordering.

### How did you avoid tuning on the test set?

The code defines disjoint tune (0–9), validation (100–104), and test
(1000–1009) seeds. Tunable policies record `tuned_on="tune"`, and the matrix
runner refuses to run an improperly marked tunable policy on the test split.

### What were the experiment dimensions?

Phase 4 has 8 workload families × 2 cluster configurations × 3 resource-time
load levels = 48 cells, with 10 test seeds and four pre-frozen policies. That
produces 1,920 held-out temporal runs. Phase 3 separately has 11 policies and
5,280 held-out admission-only runs.

### What statistics did you use?

The primary comparison is a paired per-trace policy difference against binpack.
Phase 4 reports throughput, drained P95 wait, horizon backlog, utilization, and
time-weighted active nodes. It first averages paired deltas across the
predeclared strata within each seed, then forms a two-sided 95% t-interval over
the ten seed blocks. Family/load means cover 20 pairs and overall-load means
cover 160, but the inferential sample size is ten in both cases. Per-run
nearest-rank P95 values are macro-averaged rather than pooling jobs. Family
comparisons are exploratory and unadjusted for multiplicity. Phase 3 uses its
documented paired admission analysis.

### How did Tetris perform?

At overload it completed 0.256 more jobs per simulated time unit than binpack
(95% CI `[+0.201, +0.312]`), or +1.38%. The clear gains were RAM-heavy,
bimodal, tiny/large, drift, and adversarial. At low load throughput was
identical. Near saturation Tetris was 0.036 lower overall (95% CI
`[-0.053, -0.019]`), with drift the positive exception. It is not always
better.

Phase 3 separately found 1.6–3.7 percentage-point admission gains on four
mixed-shape families. Phase 4 shows that this mechanism translates to
throughput only under some loads and shapes.

### What is the consolidation trade-off?

In Phase 4, Tetris used 5.92, 1.62, and 1.19 more time-weighted active nodes at
low, medium, and high load. Its high-load throughput improved, but drained P95
wait also rose by 0.53. The frozen hybrid retained a smaller high-load gain of
0.162 jobs/time unit for a smaller +0.86-node cost.

Phase 3's static endpoint showed the same direction more strongly: 29.4 active
nodes for Tetris versus 23.0 for binpack.

### What did the EWMA profile predict?

It maintained a recency-weighted distribution over the nine canonical
size/shape buckets. Each request was scored using only previous observations;
the profile was updated after the placement decision. Prediction error reduced
confidence and shifted future-fit weight back toward binpack.

### Why did EWMA/future-fit not help?

The current request's own shape already supplied most of the useful signal.
The stateless Tetris term captured the mixed-workload gains, while the learned
distribution rarely changed the selected node enough to improve admission.
This made added state, tuning, and scoring cost unjustified in this model.

That is a Phase 3 admission result. EWMA/future-fit was not included in the
four-policy temporal matrix, so I do not claim the temporal result is null.

### Did you cherry-pick the best policy?

No. Temporal policies and load regions were frozen on tuning seeds before the
held-out run. The report retains zero low-load gains, near-saturation losses,
higher P95 wait, and workloads whose intervals cross zero. All 1,920 raw
per-run records are retained. Phase 3 separately retains baselines, future-fit
variants, and ablations.

## Critical questions

### Isn't this just a simulator?

Yes—and that is both its value and its limitation. It provides controlled,
reproducible evidence about one placement objective. It does not prove
production impact; the next evidence level would be trace replay and then a
prototype inside or beside the real scheduler.

### Why should this matter for real Nomad?

It identifies an interpretable trade-off worth testing: request/resource shape
alignment can preserve schedulability but weaken consolidation. Because the
simulator reproduces the isolated fit formula, it is useful for hypothesis
generation. Production relevance remains unverified.

### Why didn't you modify Nomad?

The research question was initially about the behavior of one heuristic.
Isolation made mistakes easier to find and comparisons easier to pair. A real
Nomad change would add integration complexity before the mechanism had evidence.

### Are the workloads realistic?

They are controlled, not trace-calibrated. They cover balanced, skewed, mixed,
size-mixed, drifting, corrupted-signal, and deliberately awkward orderings.
Their purpose is causal stress testing; real traces are future validation.

### What would happen with job completion?

Phase 4 answers this controlled version: completion and exact release make the
Phase 3 advantage load-dependent. Low-load throughput ties, Tetris is slightly
worse near saturation overall, and it wins under overload on five shaped/mixed
families. Departures did not simply preserve or erase the earlier result.

### What about GPUs, network, storage, and constraints?

They can create additional feasibility dimensions and dominant bottlenecks.
The two-dimensional cosine and the current workload buckets would need to be
generalized, and production Nomad's complete ranking pipeline could overwhelm
the isolated fit-score effect.

### What is the biggest limitation?

The lifecycle is controlled rather than trace-calibrated: Poisson arrivals,
bounded durations, one queue discipline, and a finite 50-unit horizon. That is
good for isolating a mechanism but does not prove long-run stability or
production Nomad impact.

### What would you do with another month?

1. Replay anonymized production-like arrival, duration, and resource traces.
2. Add strict FIFO and age-aware sensitivity checks for tail-wait fairness.
3. Add bounded candidate evaluation as an explicit experimental factor.
4. Evaluate a pre-frozen multi-objective score for throughput, tail wait, and
   active nodes.
5. Only then prototype the strongest simple policy against real Nomad.
