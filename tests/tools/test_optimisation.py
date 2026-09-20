"""CP-SAT models (shelter assignment, resource matching): correct, safe, deterministic, and
degrade gracefully."""

import itertools

import pytest

from atlas.tools import optimisation
from atlas.tools.optimisation import Group, Job, Shelter, Unit, assign_people, assign_resources


def cost_matrix(rows):
    """{("g1", "s1"): 5, ...} from a compact {group: {shelter: cost}} table."""
    return {(g, s): c for g, row in rows.items() for s, c in row.items()}


def by_group(result):
    out = {}
    for g, s, n in result.placements:
        out.setdefault(g, {})[s] = n
    return out


def test_everyone_is_housed_at_the_cheapest_shelter_when_there_is_room():
    result = assign_people(
        [Group("g1", 10)],
        [Shelter("near", 50), Shelter("far", 50)],
        cost_matrix({"g1": {"near": 3, "far": 9}}),
    )
    assert result.placements == [("g1", "near", 10)]
    assert result.unhoused == {} and result.housed == 10
    assert result.status == "OPTIMAL"


def test_capacity_is_never_exceeded_and_overflow_goes_to_the_next_cheapest():
    result = assign_people(
        [Group("g1", 30), Group("g2", 30)],
        [Shelter("a", 40), Shelter("b", 40)],
        cost_matrix({"g1": {"a": 1, "b": 5}, "g2": {"a": 1, "b": 5}}),
    )
    used = {}
    for _, s, n in result.placements:
        used[s] = used.get(s, 0) + n
    assert used == {"a": 40, "b": 20}
    assert result.housed == 60 and result.unhoused == {}


def test_a_group_can_be_split_when_no_single_shelter_fits_it():
    groups, shelters = [Group("big", 150)], [Shelter("a", 100), Shelter("b", 100)]
    cost = cost_matrix({"big": {"a": 2, "b": 4}})
    split = assign_people(groups, shelters, cost, allow_split=True)
    assert by_group(split) == {"big": {"a": 100, "b": 50}} and split.unhoused == {}
    whole = assign_people(groups, shelters, cost, allow_split=False)
    assert whole.placements == [] and whole.unhoused == {"big": 150}


def test_without_splitting_each_group_goes_to_a_single_shelter():
    result = assign_people(
        [Group("g1", 30), Group("g2", 30)],
        [Shelter("a", 40), Shelter("b", 40)],
        cost_matrix({"g1": {"a": 1, "b": 1}, "g2": {"a": 1, "b": 1}}),
        allow_split=False,
    )
    assert result.housed == 60
    assert all(len(v) == 1 for v in by_group(result).values())


def test_housing_more_people_beats_a_cheaper_journey():
    """A costly shelter is used if it lets one more person in."""
    result = assign_people(
        [Group("g1", 10), Group("g2", 10)],
        [Shelter("cheap", 10), Shelter("dear", 10)],
        cost_matrix({"g1": {"cheap": 1, "dear": 900}, "g2": {"cheap": 1}}),
    )
    assert result.unhoused == {}
    assert by_group(result) == {"g1": {"dear": 10}, "g2": {"cheap": 10}}


def test_pairs_without_a_cost_are_never_used_and_leave_people_unhoused():
    result = assign_people(
        [Group("cut_off", 8), Group("ok", 5)],
        [Shelter("a", 100)],
        cost_matrix({"ok": {"a": 2}}),  # no safe route for "cut_off"
    )
    assert result.placements == [("ok", "a", 5)]
    assert result.unhoused == {"cut_off": 8}


def test_full_shelters_and_empty_groups_are_handled():
    assert assign_people([Group("g", 5)], [Shelter("a", 0)], {("g", "a"): 1}).unhoused == {"g": 5}
    assert assign_people([Group("g", 0)], [Shelter("a", 5)], {("g", "a"): 1}).placements == []
    assert assign_people([], [], {}).placements == []


def test_equal_costs_break_ties_deterministically_toward_the_first_shelter():
    result = assign_people(
        [Group("g", 5)], [Shelter("b", 10), Shelter("a", 10)], cost_matrix({"g": {"a": 3, "b": 3}})
    )
    assert result.placements == [("g", "a", 5)]


def test_the_answer_does_not_depend_on_input_order_or_repetition():
    groups = [Group("g1", 25), Group("g2", 40), Group("g3", 15)]
    shelters = [Shelter("a", 45), Shelter("b", 30), Shelter("c", 30)]
    cost = cost_matrix(
        {
            "g1": {"a": 4, "b": 6, "c": 9},
            "g2": {"a": 5, "b": 5, "c": 7},
            "g3": {"a": 8, "b": 2, "c": 3},
        }
    )
    reference = assign_people(groups, shelters, cost)
    for gs, ss in itertools.product(
        itertools.permutations(groups), itertools.permutations(shelters)
    ):
        assert assign_people(list(gs), list(ss), cost) == reference
    assert assign_people(groups, shelters, cost) == reference


def test_a_failed_solve_falls_back_to_a_safe_greedy_answer(monkeypatch):
    class Broken:
        def __init__(self):
            self.parameters = type("P", (), {})()

        def solve(self, model):
            return optimisation.cp_model.UNKNOWN

    monkeypatch.setattr(optimisation.cp_model, "CpSolver", Broken)
    result = assign_people(
        [Group("g1", 30), Group("g2", 30)],
        [Shelter("a", 40), Shelter("b", 40)],
        cost_matrix({"g1": {"a": 1, "b": 5}, "g2": {"a": 1, "b": 5}}),
    )
    assert result.status == "GREEDY_FALLBACK"
    assert result.housed == 60 and result.unhoused == {}


def test_the_independent_check_rejects_a_bad_assignment():
    bad = optimisation.ShelterAssignment([("g", "a", 99)], {}, "X")
    with pytest.raises(AssertionError):
        optimisation._check(bad, [Group("g", 99)], [Shelter("a", 10)])


@pytest.mark.parametrize(
    ("groups", "shelters", "cost"),
    [
        ([Group("g", 1), Group("g", 2)], [Shelter("a", 1)], {}),
        ([Group("g", 1)], [Shelter("a", 1), Shelter("a", 2)], {}),
        ([Group("g", -1)], [Shelter("a", 1)], {}),
        ([Group("g", 1)], [Shelter("a", -1)], {}),
        ([Group("g", 1)], [Shelter("a", 1)], {("g", "a"): -3}),
    ],
)
def test_invalid_input_is_rejected(groups, shelters, cost):
    with pytest.raises(ValueError):
        assign_people(groups, shelters, cost)


# ============================================================ resources -> jobs (A6)


def team(i):
    return Unit(f"team-{i}", "rescue_team")


def van(i):
    return Unit(f"van-{i}", "vehicle")


def need(**kinds):
    return tuple(sorted(kinds.items()))


def crew_of(result, job_id):
    return sorted(u for j, u in result.assignments if j == job_id)


def test_jobs_get_their_cheapest_units_and_all_are_served_when_there_are_enough():
    result = assign_resources(
        [team(1), team(2), van(1)],
        [Job("a", 5, need(rescue_team=1, vehicle=1)), Job("b", 3, need(rescue_team=1))],
        {
            ("team-1", "a"): 10,
            ("team-2", "a"): 20,
            ("van-1", "a"): 5,
            ("team-1", "b"): 3,
            ("team-2", "b"): 4,
        },
    )
    assert result.status == "OPTIMAL" and result.unserved == []
    assert crew_of(result, "a") == ["team-1", "van-1"] and crew_of(result, "b") == ["team-2"]


def test_a_job_is_served_completely_or_not_at_all():
    """One team for a job that needs two: nobody is sent, the team stays free for another job."""
    result = assign_resources(
        [team(1)],
        [Job("needs_two", 9, need(rescue_team=2)), Job("needs_one", 1, need(rescue_team=1))],
        {("team-1", "needs_two"): 1, ("team-1", "needs_one"): 50},
    )
    assert result.served == ["needs_one"] and result.unserved == ["needs_two"]
    assert crew_of(result, "needs_two") == []


def test_more_valuable_jobs_are_served_first_when_units_are_short():
    result = assign_resources(
        [team(1)],
        [Job("low", 2, need(rescue_team=1)), Job("high", 8, need(rescue_team=1))],
        {("team-1", "low"): 1, ("team-1", "high"): 900},  # high is far away, yet still wins
    )
    assert result.served == ["high"] and result.unserved == ["low"]


def test_weight_is_a_value_not_a_strict_rank():
    """Two weight-3 jobs (total 6) beat one weight-5 job when they compete for the same units."""
    result = assign_resources(
        [team(1), team(2)],
        [
            Job("big", 5, need(rescue_team=2)),
            Job("s1", 3, need(rescue_team=1)),
            Job("s2", 3, need(rescue_team=1)),
        ],
        {(t, j): 1 for t in ("team-1", "team-2") for j in ("big", "s1", "s2")},
    )
    assert result.served == ["s1", "s2"] and result.unserved == ["big"]


def test_unreachable_units_are_never_sent():
    result = assign_resources(
        [team(1), team(2)],
        [Job("a", 5, need(rescue_team=1))],
        {("team-2", "a"): 90},  # team-1 has no safe route
    )
    assert crew_of(result, "a") == ["team-2"]
    none = assign_resources([team(1)], [Job("a", 5, need(rescue_team=1))], {})
    assert none.served == [] and none.unserved == ["a"]


def test_units_of_the_wrong_kind_are_not_used():
    result = assign_resources([van(1)], [Job("a", 5, need(rescue_team=1))], {("van-1", "a"): 1})
    assert result.assignments == [] and result.unserved == ["a"]


def test_no_unit_serves_two_jobs():
    result = assign_resources(
        [team(1), team(2)],
        [Job(f"j{i}", 4, need(rescue_team=1)) for i in range(3)],
        {(t, f"j{i}"): 1 + i for t in ("team-1", "team-2") for i in range(3)},
    )
    used = [u for _, u in result.assignments]
    assert len(used) == len(set(used)) == 2 and len(result.unserved) == 1


def test_resource_answers_are_deterministic_and_independent_of_input_order():
    units = [team(1), team(2), team(3), van(1), van(2)]
    jobs = [
        Job("a", 6, need(rescue_team=2, vehicle=1)),
        Job("b", 4, need(rescue_team=1, vehicle=1)),
        Job("c", 2, need(vehicle=1)),
    ]
    cost = {
        (u.id, j.id): 3 + 2 * (i + k) % 7
        for i, u in enumerate(units)
        for k, j in enumerate(jobs)
        if any(kind == u.kind for kind, _ in j.needs)
    }
    reference = assign_resources(units, jobs, cost)
    assert assign_resources(units, jobs, cost) == reference
    for us, js in itertools.product(
        itertools.islice(itertools.permutations(units), 12), itertools.permutations(jobs)
    ):
        assert assign_resources(list(us), list(js), cost) == reference


def test_a_failed_resource_solve_falls_back_to_a_safe_greedy_answer(monkeypatch):
    class Broken:
        def __init__(self):
            self.parameters = type("P", (), {})()

        def solve(self, model):
            return optimisation.cp_model.UNKNOWN

    monkeypatch.setattr(optimisation.cp_model, "CpSolver", Broken)
    result = assign_resources(
        [team(1), team(2)],
        [Job("a", 8, need(rescue_team=1)), Job("b", 2, need(rescue_team=1))],
        {("team-1", "a"): 5, ("team-2", "a"): 1, ("team-1", "b"): 1, ("team-2", "b"): 9},
    )
    assert result.status == "GREEDY_FALLBACK"
    assert result.served == ["a", "b"] and crew_of(result, "a") == ["team-2"]


def test_the_resource_check_rejects_bad_crews_and_double_booking():
    units, jobs = [team(1), team(2)], [Job("a", 1, need(rescue_team=1))]
    double = optimisation.ResourceAssignment([("a", "team-1"), ("b", "team-1")], [], [], "X")
    with pytest.raises(AssertionError, match="more than one job"):
        optimisation._check_resources(double, units, jobs)
    wrong = optimisation.ResourceAssignment([("a", "team-1"), ("a", "team-2")], [], [], "X")
    with pytest.raises(AssertionError, match="exactly what it needs"):
        optimisation._check_resources(wrong, units, jobs)


@pytest.mark.parametrize(
    ("units", "jobs", "cost"),
    [
        ([team(1), team(1)], [Job("a", 1, need(rescue_team=1))], {}),
        ([team(1)], [Job("a", 1, need(rescue_team=1)), Job("a", 1, need(rescue_team=1))], {}),
        ([team(1)], [Job("a", 0, need(rescue_team=1))], {}),
        ([team(1)], [Job("a", 1, ())], {}),
        ([team(1)], [Job("a", 1, (("rescue_team", 0),))], {}),
        ([team(1)], [Job("a", 1, (("rescue_team", 1), ("rescue_team", 1)))], {}),
        ([team(1)], [Job("a", 1, need(rescue_team=1))], {("team-1", "a"): -1}),
    ],
)
def test_invalid_resource_input_is_rejected(units, jobs, cost):
    with pytest.raises(ValueError):
        assign_resources(units, jobs, cost)
