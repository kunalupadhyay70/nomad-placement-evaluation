import pytest

from sim.models import Job, NodeState
from sim.policies import binpack, make_binpack_tunable, spread

RESOURCE_STATES = [
    NodeState("n-empty", total_cpu=100, total_ram=100, used_cpu=0, used_ram=0),
    NodeState("n-full", total_cpu=100, total_ram=100, used_cpu=100, used_ram=100),
    NodeState("n-half", total_cpu=100, total_ram=100, used_cpu=50, used_ram=50),
    NodeState("n-balanced-2080", total_cpu=100, total_ram=100, used_cpu=80, used_ram=80),
    NodeState("n-asym", total_cpu=200, total_ram=50, used_cpu=40, used_ram=45),
]

JOB = Job("job-1", cpu=1, ram=1, shape="generic")


def _node(used_cpu: float, used_ram: float) -> NodeState:
    return NodeState("n", total_cpu=100, total_ram=100, used_cpu=used_cpu, used_ram=used_ram)


# --- Requirement 1 & 2: empty node boundary -------------------------------


def test_empty_node_binpack_is_zero():
    node = _node(used_cpu=0, used_ram=0)
    assert binpack(node, JOB) == 0.0


def test_empty_node_spread_is_one():
    node = _node(used_cpu=0, used_ram=0)
    assert spread(node, JOB) == 1.0


# --- Requirement 3 & 4: perfect fit boundary -------------------------------


def test_perfect_fit_binpack_is_one():
    node = _node(used_cpu=100, used_ram=100)
    assert binpack(node, JOB) == 1.0


def test_perfect_fit_spread_is_zero():
    node = _node(used_cpu=100, used_ram=100)
    assert spread(node, JOB) == 0.0


# --- Requirement 5: tunable base 10 equals fixed binpack exactly ----------


@pytest.mark.parametrize("node", RESOURCE_STATES)
def test_tunable_base_ten_equals_binpack(node):
    tunable_base_ten = make_binpack_tunable(10.0)
    assert tunable_base_ten(node, JOB) == binpack(node, JOB)


# --- Requirement 6: balanced leftovers outrank skewed under bin-pack -----


def test_binpack_prefers_balanced_leftovers_over_skewed():
    balanced = _node(used_cpu=50, used_ram=50)  # 50/50 free
    skewed = _node(used_cpu=80, used_ram=20)  # 20/80 free
    assert binpack(balanced, JOB) > binpack(skewed, JOB)


# --- Requirement 7: skewed leftovers outrank balanced under spread -------


def test_spread_prefers_skewed_leftovers_over_balanced():
    balanced = _node(used_cpu=50, used_ram=50)  # 50/50 free
    skewed = _node(used_cpu=80, used_ram=20)  # 20/80 free
    assert spread(skewed, JOB) > spread(balanced, JOB)


# --- Requirement 8: scoring is pure / leaves inputs unchanged -------------


@pytest.mark.parametrize(
    "policy",
    [binpack, spread, make_binpack_tunable(4.0)],
    ids=["binpack", "spread", "tunable-base-4"],
)
def test_scoring_does_not_mutate_node_or_job(policy):
    node = _node(used_cpu=37, used_ram=61)
    job = Job("job-2", cpu=3, ram=5, shape="generic")

    node_before = (node.node_id, node.total_cpu, node.total_ram, node.used_cpu, node.used_ram)
    job_before = (job.job_id, job.cpu, job.ram, job.shape)

    policy(node, job)

    node_after = (node.node_id, node.total_cpu, node.total_ram, node.used_cpu, node.used_ram)
    job_after = (job.job_id, job.cpu, job.ram, job.shape)

    assert node_before == node_after
    assert job_before == job_after

    # Immutability is structural, not just behavioral: mutation must raise.
    with pytest.raises(AttributeError):
        node.used_cpu = 999  # type: ignore[misc]
    with pytest.raises(AttributeError):
        job.cpu = 999  # type: ignore[misc]


# --- Requirement 9: invalid bases raise ValueError ------------------------


@pytest.mark.parametrize("bad_base", [1, 0, -1, -10.5])
def test_invalid_base_raises_value_error(bad_base):
    with pytest.raises(ValueError):
        make_binpack_tunable(bad_base)
