# Interview guide

Use the first sentence of each answer for a recruiter or generalist. Continue
with the technical detail only when the interviewer asks.

## Thirty-second explanation

I built a deterministic online CPU/RAM placement simulator to isolate
HashiCorp Nomad's fit-scoring heuristic. I compared Nomad-style bin packing
with resource-shape-aware policies on paired seeded workloads. Tetris-style
alignment admitted 1.6–3.7 percentage points more requests on four mixed-shape
workloads, but used about six more nodes on average; a more complex EWMA
prediction extension added no material benefit.

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

It is not throughput: jobs have no duration, completion, or resource release,
so the simulator cannot measure completed work per unit time.

## What I implemented

### What exactly did you build?

I built an online sequential two-resource placement simulator with immutable
node/job models, feasibility filtering, pluggable scoring policies, seeded
cluster and workload generation, detailed and lean experiment runners,
paired statistics, CSV artifacts, and reproducible plots.

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
arrival order. Stateful policies get a fresh instance for every run. Tests
verify determinism and no cross-policy mutation.

### Why multiple seeds?

Cluster capacity draws and request order affect greedy placement. Ten held-out
seeds show whether a difference repeats rather than depending on one favorable
ordering.

### How did you avoid tuning on the test set?

The code defines disjoint tune (0–9), validation (100–104), and test
(1000–1009) seeds. Tunable policies record `tuned_on="tune"`, and the matrix
runner refuses to run an improperly marked tunable policy on the test split.

### What were the experiment dimensions?

The held-out study has 8 workload families × 2 cluster configurations × 3 load
levels = 48 cells, with 10 test seeds and 11 policies/ablations. That produces
5,280 held-out policy runs.

### What statistics did you use?

The primary comparison is the paired per-trace admission difference between a
candidate and binpack. Reports include mean difference, standard deviation,
95% paired t-interval, and wins/losses/ties. Per-family results pool 60 paired
observations: two clusters × three loads × ten seeds.

### How did Tetris perform?

It improved mean admission by 1.6–3.7 percentage points on bimodal, tiny/large,
drift, and adversarial mixed-shape families; those paired intervals excluded
zero. It was 0.23 points lower on CPU-heavy workloads, with an interval that
included zero. It is not always better.

### What is the consolidation trade-off?

Across all held-out traces, binpack used 23.0 of 30 nodes on average and Tetris
used 29.4. Tetris admitted more in several mixed workloads, while binpack left
more whole nodes idle. The right objective depends on whether future
schedulability or consolidation is more valuable.

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

### Did you cherry-pick the best policy?

No. The final matrix includes baselines, tuned policies, future-fit variants,
and ablations on disjoint test seeds. The report retains CPU-heavy losses and
the prediction null result. The selected parameters and raw per-run records are
saved.

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

Departures would continually reshape free capacity and could reduce or reverse
the advantage observed in a monotonically filling cluster. They would also make
forecasting more meaningful because future availability would depend on both
arrivals and departures. The current study cannot answer this.

### What about GPUs, network, storage, and constraints?

They can create additional feasibility dimensions and dominant bottlenecks.
The two-dimensional cosine and the current workload buckets would need to be
generalized, and production Nomad's complete ranking pipeline could overwhelm
the isolated fit-score effect.

### What is the biggest limitation?

No durations or resource release. The cluster only fills, so the result is an
admission study under sequential arrivals, not a steady-state scheduler model.

### What would you do with another month?

1. Add seeded durations, completion events, resource release, and a queue while
   preserving paired trace replay.
2. Calibrate workload distributions from an anonymized real trace.
3. Add bounded candidate evaluation as an explicit experimental factor.
4. Evaluate a multi-objective score that prices active nodes and admission.
5. Only then prototype the strongest simple policy against real Nomad.

