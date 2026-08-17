# Phase-3 pre-implementation audit

Conducted before any Phase-3 code was written (2026-08-07).

## Repository state found

All six `sim/` modules, 5 test files (73 tests, all passing), smoke
scripts, and reports were present and healthy. Nothing was incorrect;
nothing working was rewritten. Findings:

1. **Baseline verified against upstream** (`hashicorp/nomad` commit
   `f3fe893c53d20681232700eb67f89f7478c2fa4e`, rechecked 2026-08-18):
   `ScoreFitBinPack` = `clamp(20 − (10^fc + 10^fr), 0, 18)` and
   `ScoreFitSpread` = `clamp((10^fc + 10^fr) − 2, 0, 18)` match
   `sim/policies.py` exactly, including normalization by 18
   (`binPackingMaxFitScore` in `scheduler/feasible/rank.go`). Upstream
   computes free fractions against capacity minus reserved resources
   (reserved modeled as 0 here) on post-placement utilization, which
   `sim/scheduler.py` mirrors. The convex score is not
   fragmentation-blind and is never described as such.
2. **No arrival times, durations, completions, or queue existed** (still
   true after Phase 3, by scope decision). All results are therefore
   admission-rate results and are labeled as such everywhere;
   completed-job throughput is explicitly out of scope until a
   durations phase.
3. **Missing pieces** (implemented in Phase 3): experimental policies
   (alignment, hybrid, mismatch-penalty, future-fit), EWMA workload
   profile, multi-seed experiment matrix, seed discipline, paired
   statistics, latency measurement, CSV/plot pipeline, and tests for
   all of the above (73 → 126 tests at Phase-3 completion; 127 at release).
4. **Duplication noted**: `nomad-placement-lab-corrected/` is a
   byte-identical copy of this directory, and three historical zips sit
   beside it. Left untouched; consider deleting the copy to avoid
   divergence.
5. **Simulator-vs-Nomad simplifications carried forward** (documented in
   README and DESIGN.md): no constraints/affinities/devices/networks/
   preemption, and every feasible node is scored (real Nomad samples
   candidates).
