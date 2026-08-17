from sim.models import Job, NodeState
from sim.policies import binpack, spread
from sim.scheduler import fits, hypothetical_placement, place, run_schedule


def _node(node_id, total_cpu=100, total_ram=100, used_cpu=0, used_ram=0):
    return NodeState(node_id, total_cpu=total_cpu, total_ram=total_ram, used_cpu=used_cpu, used_ram=used_ram)


def test_fits_true_when_capacity_available():
    node = _node("n1", used_cpu=50, used_ram=50)
    job = Job("j1", cpu=40, ram=40, shape="x")
    assert fits(node, job) is True


def test_fits_false_when_over_capacity():
    node = _node("n1", used_cpu=50, used_ram=50)
    job = Job("j1", cpu=60, ram=10, shape="x")
    assert fits(node, job) is False


def test_hypothetical_placement_does_not_mutate_input():
    node = _node("n1", used_cpu=10, used_ram=10)
    job = Job("j1", cpu=5, ram=5, shape="x")
    new_node = hypothetical_placement(node, job)
    assert node.used_cpu == 10 and node.used_ram == 10  # original untouched
    assert new_node.used_cpu == 15 and new_node.used_ram == 15


def test_place_picks_highest_scoring_feasible_node_binpack():
    # Under binpack, the tighter-fitting node (less free space after
    # placement) should win.
    tight = _node("tight", used_cpu=90, used_ram=90)   # will be nearly full
    loose = _node("loose", used_cpu=0, used_ram=0)      # will be nearly empty
    job = Job("j1", cpu=5, ram=5, shape="x")

    result, new_cluster = place([tight, loose], job, binpack)

    assert result.placed
    assert result.node_id == "tight"
    assert result.feasible_node_count == 2


def test_place_picks_highest_scoring_feasible_node_spread():
    tight = _node("tight", used_cpu=90, used_ram=90)
    loose = _node("loose", used_cpu=0, used_ram=0)
    job = Job("j1", cpu=5, ram=5, shape="x")

    result, new_cluster = place([tight, loose], job, spread)

    assert result.placed
    assert result.node_id == "loose"


def test_place_returns_unplaced_when_no_feasible_node():
    full = _node("full", used_cpu=100, used_ram=100)
    job = Job("j1", cpu=1, ram=1, shape="x")

    result, new_cluster = place([full], job, binpack)

    assert result.placed is False
    assert result.node_id is None
    assert result.score is None
    assert result.feasible_node_count == 0
    assert result.tied_candidate_count == 0
    # cluster unchanged
    assert new_cluster[0].used_cpu == 100 and new_cluster[0].used_ram == 100


def test_infeasible_nodes_are_skipped_before_scoring():
    """Feasibility filtering must happen before the policy is called."""
    infeasible = _node("infeasible", used_cpu=100, used_ram=100)
    feasible = _node("feasible", used_cpu=20, used_ram=20)
    job = Job("j1", cpu=5, ram=5, shape="x")
    scored_node_ids = []

    def recording_policy(node, _job):
        scored_node_ids.append(node.node_id)
        return 1.0

    result, _ = place([infeasible, feasible], job, recording_policy)

    assert result.node_id == "feasible"
    assert result.feasible_node_count == 1
    assert scored_node_ids == ["feasible"]


# --- Tie-breaking (METHODOLOGY CORRECTION) ---------------------------------
#
# Two identical nodes produce identical scores under any of these policies.
# Ties must be broken by earliest position in the *input sequence*, not by
# node_id -- node_id must have no bearing on the outcome.


def test_tie_broken_by_earliest_input_position_not_node_id():
    # "node-b" is listed FIRST but has a lexically LARGER id than "node-a"
    # which is listed second. The old node_id-sort tiebreak would pick
    # "node-a"; the corrected input-order tiebreak must pick "node-b".
    a = _node("node-b")
    b = _node("node-a")
    job = Job("j1", cpu=5, ram=5, shape="x")

    result, _ = place([a, b], job, binpack)
    assert result.node_id == "node-b"
    assert result.tied_candidate_count == 2


def test_tie_result_unaffected_by_renaming_node_ids():
    a = _node("first")
    b = _node("second")
    job = Job("j1", cpu=5, ram=5, shape="x")

    result, _ = place([a, b], job, binpack)
    assert result.node_id == "first"  # earliest in sequence, regardless of name


def test_tie_winner_changes_when_sequence_is_reordered():
    a = _node("alpha")
    b = _node("beta")
    job = Job("j1", cpu=5, ram=5, shape="x")

    result_ab, _ = place([a, b], job, binpack)
    result_ba, _ = place([b, a], job, binpack)

    assert result_ab.node_id == "alpha"
    assert result_ba.node_id == "beta"


def test_score_tie_counted_correctly():
    # Three identical nodes -> 3-way tie.
    nodes = [_node(f"n{i}") for i in range(3)]
    job = Job("j1", cpu=5, ram=5, shape="x")

    result, _ = place(nodes, job, binpack)
    assert result.tied_candidate_count == 3
    assert result.was_tie is True


def test_no_tie_when_scores_differ():
    tight = _node("tight", used_cpu=90, used_ram=90)
    loose = _node("loose", used_cpu=0, used_ram=0)
    job = Job("j1", cpu=5, ram=5, shape="x")

    result, _ = place([tight, loose], job, binpack)
    assert result.tied_candidate_count == 1
    assert result.was_tie is False


def test_run_schedule_applies_jobs_sequentially_and_updates_cluster():
    cluster = (_node("n1", total_cpu=100, total_ram=100),)
    jobs = [Job(f"j{i}", cpu=10, ram=10, shape="x") for i in range(5)]

    final_cluster, results = run_schedule(cluster, jobs, binpack)

    assert len(results) == 5
    assert all(r.placed for r in results)
    assert final_cluster[0].used_cpu == 50
    assert final_cluster[0].used_ram == 50
    # original cluster object's node untouched
    assert cluster[0].used_cpu == 0


def test_run_schedule_records_failures_once_cluster_is_full():
    cluster = (_node("n1", total_cpu=10, total_ram=10),)
    jobs = [Job(f"j{i}", cpu=6, ram=6, shape="x") for i in range(3)]

    final_cluster, results = run_schedule(cluster, jobs, binpack)

    assert results[0].placed is True
    assert results[1].placed is False
    assert results[2].placed is False
