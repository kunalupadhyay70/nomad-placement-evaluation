# Design: workload-aware placement policies and experiment protocol

This document specifies every policy, formula, metric, and experimental
control used in the Phase-3 matrix study. Code: `sim/experimental.py`,
`sim/profile.py`, `sim/families.py`, `sim/matrix.py`,
`scripts/run_matrix.py`, `scripts/analyze_matrix.py`.

## 0. Scope and simulator state

The simulator is **admission-only**: jobs arrive in sequence, are placed
greedily or rejected, and never depart. There are **no arrival times, no
durations, no completions, and no waiting queue**. Consequently every
"admission rate" below is a placement/admission measure and must NOT be
read as completed-job throughput; throughput requires durations and
departures (future phase). Real Nomad details not modeled: constraints,
affinities, devices, networks, preemption, candidate sampling, reserved
resources (modeled as 0). The Nomad fit formulas themselves are verified
against `hashicorp/nomad` commit
`f3fe893c53d20681232700eb67f89f7478c2fa4e`
(`nomad/structs/funcs.go`: `ScoreFitBinPack`, `ScoreFitSpread`;
normalization by 18 as in `scheduler/feasible/rank.go`). See
`docs/NOMAD_FIDELITY.md` for immutable source links.

Score contract: every policy maps a hypothetical **post-placement** node
state and the job to a score in [0, 1]; the scheduler places on the
highest-scoring feasible node, ties broken by input-sequence position
(see `sim/scheduler.py`).

## 1. Policies

### A. Nomad bin-pack (primary baseline) and spread

    fc, fr  = post-placement free fractions
    binpack = clamp(20 - (10^fc + 10^fr), 0, 18) / 18
    spread  = clamp((10^fc + 10^fr) - 2,  0, 18) / 18

Exact upstream formulas; untouched from Phase 1. Note the binpack score
is convex in (fc, fr): it already prefers certain balanced, tightly
packed residuals. It is *not* fragmentation-blind.

### B. Tetris-style alignment (stateless)

    free_pre = [(total_cpu - used_cpu_pre)/total_cpu,
                (total_ram - used_ram_pre)/total_ram]
    demand   = [job_cpu/total_cpu, job_ram/total_ram]
    tetris   = cos(free_pre, demand);  0 if either norm is 0

Pre-placement free is reconstructed exactly from the post-placement
state by subtracting the job's demand. **Pre-placement is a deliberate
choice** (Tetris, Grandl et al. SIGCOMM'14): score the match between
what the job needs and what the node has, so CPU-heavy jobs go to
CPU-abundant nodes. Post-placement residual usefulness is what the
future-fit term (D) measures; keeping alignment pre-placement keeps the
two non-redundant. Both vectors are componentwise >= 0, so cosine is in
[0, 1].

### C. Hybrid

    hybrid = alpha * binpack + beta * tetris,  alpha + beta = 1

alpha tuned on training seeds only.

### Balance-aware bin-pack (simple penalty baseline)

    balance = clamp(binpack - lam * |fc - fr|, 0, 1)

Workload-agnostic residual-balance penalty; lam tuned on training seeds.

### Workload-mismatch penalty ("recommended simple heuristic")

    L_c      = fc / (fc + fr)                (leftover CPU share, capacity-normalized)
    D_c      = EWMA-profile mean demand CPU share
    mismatch = (|L_c - D_c| + |L_r - D_r|) / 2 = |L_c - D_c|
    wmatch   = clamp(binpack - lam_eff * mismatch, 0, 1)
    lam_eff  = lam * profile.confidence      (fallback on by default)

### D. Workload-aware future-fit

    score = alpha_eff * binpack + beta * tetris + gamma_eff * future_fit
    alpha + beta + gamma = 1 (validated, tolerance 1e-9)
    gamma_eff = gamma * profile.confidence;  alpha_eff = alpha + (gamma - gamma_eff)

Future-fit variants, evaluated on the candidate node's residual only —
for a single job all candidates leave the rest of the cluster identical,
so cluster-wide constant terms cannot change the argmax and are dropped:

    D1 cheap:     ff1(n) = sum_b p(b) * 1[rep_b fits residual(n)]
    D2 capacity:  ff2(n) = sum_b p(b) * min(k_b(n), CAP)/CAP,  CAP = 3
                  k_b(n) = min(floor(free_cpu/cpu_b), floor(free_ram/ram_b))

Buckets b: the canonical 9 (size_class x shape) classes. This is
generator-aligned discretization; a quantile scheme would re-derive the
same boundaries because the generator emits exactly these classes. The
bucket set is a constructor parameter, so other discretizations remain
possible. D-family model selection is constrained to gamma > 0: the
gamma = 0 corners do not use the future-fit term and are already
represented by the hybrid/tetris families.

### EWMA workload profile and confidence

    p_{t+1}(b) = (1 - eta) p_t(b) + eta * 1[b = b_t]
    err_{t+1}  = (1 - eta) err_t + eta * (1 - p_t(b_t))     (pre-update)
    confidence = clamp(1 - err / (1 - 1/B), 0, 1)

Predict-then-update: job t is scored by a profile that has seen only
jobs < t. A fresh profile starts at confidence 0 (chance-level error),
so the workload-aware term must earn trust. An EWMA was chosen over a
sliding window for O(1) memory/update and equivalent recency behavior;
eta plays the window-length role (sensitivity swept).

### Known modeling caveat

CPU and RAM units are not physically commensurable. All node-side
comparisons are capacity-normalized fractions; the one place raw units
mix is the job-shape share cpu/(cpu+ram) inside profile demand shares,
documented as a simplification (consistent across all compared policies,
so it cannot bias the paired comparison).

## 2. Workload families (sim/families.py)

30-node clusters; `homog` = identical nodes (96 CPU, 160 RAM), `hetero`
= existing repo ranges (CPU 64–128, RAM 64–256), seeded. Offered load
rho in {low 0.4, med 0.75, high 1.1} defined as
max(n*m_cpu/C_cpu, n*m_ram/C_ram); high load guarantees rejections.
Counts by largest-remainder rounding; all traces deterministic in
(family, cluster, load, seed).

| family | composition | order |
|---|---|---|
| balanced | 100% balanced shapes | shuffled |
| cpu_heavy | 80% cpu_heavy / 20% balanced | shuffled |
| ram_heavy | 80% ram_heavy / 20% balanced | shuffled |
| bimodal | 45/45/10 cpu/ram/balanced | shuffled |
| tiny_large | 70% small, 30% large; mixed shapes | shuffled |
| drift | first half cpu-mix, second half ram-mix | shuffled per half |
| pred_error | 70% cpu_heavy / 30% balanced; policy OBSERVES cpu<->ram flipped jobs | shuffled |
| adversarial | bimodal mix; constructed order | largest-first, shapes alternating |

The adversarial order is a documented heuristic construction (not a
proven worst case). The pred_error family gives the profile a maximally
wrong signal (learns "RAM-heavy future" while reality is CPU-heavy) to
test the confidence fallback.

## 3. Seed discipline and tuning

    TUNE  = 0..9        VAL = 100..104       TEST = 1000..1009 (disjoint)

Tuning cells: {bimodal, drift} x {homog, hetero} x {med, high} — the
mixed-shape workloads the hypothesis is about; stationary families are
excluded from tuning so overfitting to mixed shapes is detectable on the
held-out matrix. Grids: hybrid alpha 0.1..0.9 step 0.1; balance/wmatch
lam in {0.05, 0.1, 0.2, 0.4, 0.8}; D simplex step 0.2 with gamma <= 0.6.
Selection: highest mean per-seed paired admission delta vs. binpack,
ties toward simplicity. `sim.matrix.run_grid(split="test")` refuses
tunable configs not marked `tuned_on="tune"` — final CSVs cannot be
produced from weights fitted elsewhere. Final: all 8 families x 2
clusters x 3 loads = 48 cells x 10 test seeds, identical traces for all
policies.

## 4. Metrics (per run; sim/matrix.py)

    admission_rate = placed / submitted          (NOT throughput; see §0)
    mean_r_util    = mean_n used_r(n)/total_r(n) (final state, r in {cpu, ram})
    active_nodes   = #{n : any usage} at end
    free_imbalance = mean_n |fc(n) - fr(n)|
    stranded_frac  = sum_{n in S} (free_cpu + free_ram) / sum_n (cpu+ram capacity),
                     S = nodes that cannot fit the smallest job class in the trace
    latency        = wall time per full placement decision (feasibility +
                     scoring + update + observe); mean, p95, decisions/sec

Statistics: all comparisons are per-seed PAIRED deltas (identical
traces), reported as mean, sd, 95% t-interval, win/loss/tie counts.
Ties are exact-equality (common at low load where both policies admit
100%).

## 5. Pre-registered decision rule

Recommend the SIMPLEST policy that (1) shows repeatable paired
improvement on held-out mixed-shape cells, (2) loses no stationary
family by more than 1 percentage point of admission, (3) does not
collapse on pred_error, (4) stays within 5x baseline decision latency,
(5) is explainable in one paragraph. Rollout (E) and any neural scorer
are out of scope for this phase and must not be recommended without
meeting the same bar.
