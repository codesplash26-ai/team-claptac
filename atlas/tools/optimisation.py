"""OR-Tools CP-SAT models for shelter assignment (A7) and, later, resource allocation (A6).

Safety-relevant numbers come from these deterministic solvers, never from an LLM. Each model:

* uses integer costs, one worker and a fixed seed, so the same input gives the same answer;
* is independent of input ordering (groups and shelters are processed in sorted-id order);
* has a hard time limit and a deterministic greedy fallback, so a slow or failed solve degrades the
  answer instead of stopping the agent;
* never houses more people than a shelter has room for (a hard constraint, also re-checked).

Pure functions: no COP, no bus, no network.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

# Housing a person must always beat any travel-cost saving. Costs are scaled by COST_SCALE, so a
# single person's cost stays far below this.
UNHOUSED_PENALTY = 10_000_000
COST_SCALE = 100


@dataclass(frozen=True)
class Group:
    id: str
    people: int


@dataclass(frozen=True)
class Shelter:
    id: str
    free: int  # places still available


@dataclass
class ShelterAssignment:
    placements: list[tuple[str, str, int]]  # (group id, shelter id, people), sorted
    unhoused: dict[str, int]  # group id -> people not placed
    status: str  # OPTIMAL, FEASIBLE, or GREEDY_FALLBACK
    objective: int | None = None
    solve_seconds: float = field(default=0.0, compare=False)  # wall time; excluded from equality

    @property
    def housed(self) -> int:
        return sum(p for _, _, p in self.placements)


def _validate(groups, shelters, cost):
    for label, items in (("group", groups), ("shelter", shelters)):
        ids = [i.id for i in items]
        if len(set(ids)) != len(ids):
            raise ValueError(f"Duplicate {label} id")
    if any(g.people < 0 for g in groups) or any(s.free < 0 for s in shelters):
        raise ValueError("people and free places must be non-negative")
    if any(c < 0 for c in cost.values()):
        raise ValueError("costs must be non-negative")


def assign_people(
    groups: list[Group],
    shelters: list[Shelter],
    cost: dict[tuple[str, str], int],
    *,
    allow_split: bool = True,
    time_limit_seconds: float = 2.0,
    seed: int = 0,
) -> ShelterAssignment:
    """Place people into shelters, housing as many as possible, then minimising travel cost.

    `cost[(group id, shelter id)]` is the per-person integer cost of that pair. A missing pair means
    the group cannot safely reach that shelter, so it is never used. With `allow_split` a group may
    be spread over several shelters; otherwise each group goes wholly to one shelter or is unhoused.
    """
    _validate(groups, shelters, cost)
    groups = sorted(groups, key=lambda g: g.id)
    shelters = sorted(shelters, key=lambda s: s.id)
    started = time.perf_counter()

    model = cp_model.CpModel()
    expr: dict[tuple[str, str], object] = {}
    for g in groups:
        for s in shelters:
            if (g.id, s.id) not in cost or s.free <= 0 or g.people <= 0:
                continue
            if allow_split:
                expr[g.id, s.id] = model.new_int_var(0, min(g.people, s.free), f"y_{g.id}_{s.id}")
            elif g.people <= s.free:
                expr[g.id, s.id] = g.people * model.new_bool_var(f"x_{g.id}_{s.id}")
    for g in groups:
        mine = [expr[g.id, s.id] for s in shelters if (g.id, s.id) in expr]
        if mine:
            # Without splitting each choice is `people * bool`, so this also allows only one shelter.
            model.add(sum(mine) <= g.people)
    for s in shelters:
        there = [expr[g.id, s.id] for g in groups if (g.id, s.id) in expr]
        if there:
            model.add(sum(there) <= s.free)

    # Minimise: travel cost of placed people, minus a large reward per person housed. The shelter
    # rank breaks exact ties in favour of the alphabetically first shelter, deterministically.
    rank = {s.id: i for i, s in enumerate(shelters)}
    model.minimize(
        sum(
            (COST_SCALE * cost[g_id, s_id] + rank[s_id] - UNHOUSED_PENALTY) * var
            for (g_id, s_id), var in expr.items()
        )
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = seed
    status = solver.solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        placements = sorted(
            (g_id, s_id, solver.value(var))
            for (g_id, s_id), var in expr.items()
            if solver.value(var) > 0
        )
        result = _finish(groups, placements, solver.status_name(status))
        result.objective = int(solver.objective_value)
    else:
        result = _greedy(groups, shelters, cost, allow_split)
    result.solve_seconds = time.perf_counter() - started
    _check(result, groups, shelters)
    return result


def _finish(groups, placements, status) -> ShelterAssignment:
    placed: dict[str, int] = {}
    for g_id, _, people in placements:
        placed[g_id] = placed.get(g_id, 0) + people
    unhoused = {
        g.id: g.people - placed.get(g.id, 0) for g in groups if g.people - placed.get(g.id, 0) > 0
    }
    return ShelterAssignment(placements, unhoused, status)


def _greedy(groups, shelters, cost, allow_split) -> ShelterAssignment:
    """Deterministic fallback: each group, in id order, takes the cheapest shelter with room."""
    free = {s.id: s.free for s in shelters}
    placements = []
    for g in groups:
        remaining = g.people
        for s_id in sorted(
            (s.id for s in shelters), key=lambda i: (cost.get((g.id, i), 10**12), i)
        ):
            if (g.id, s_id) not in cost or free[s_id] <= 0 or remaining <= 0:
                continue
            take = (
                min(remaining, free[s_id])
                if allow_split
                else (remaining if remaining <= free[s_id] else 0)
            )
            if take > 0:
                placements.append((g.id, s_id, take))
                free[s_id] -= take
                remaining -= take
                if not allow_split:
                    break
    return _finish(groups, sorted(placements), "GREEDY_FALLBACK")


def _check(result: ShelterAssignment, groups, shelters) -> None:
    """Independent re-check of the hard constraints, whatever produced the answer."""
    free = {s.id: s.free for s in shelters}
    people = {g.id: g.people for g in groups}
    used: dict[str, int] = {}
    placed: dict[str, int] = {}
    for g_id, s_id, n in result.placements:
        used[s_id] = used.get(s_id, 0) + n
        placed[g_id] = placed.get(g_id, 0) + n
    if any(used[s] > free[s] for s in used) or any(placed[g] > people[g] for g in placed):
        raise AssertionError("assignment violates a capacity or demand constraint")


# ---------------------------------------------------------------- resources -> jobs (A6)

# A job's weight must dominate any travel-cost saving, so serving a job always beats a cheaper
# journey. Weights are integers (callers scale their priorities); costs are scaled by COST_SCALE.
WEIGHT_SCALE = 10**9


@dataclass(frozen=True)
class Unit:
    id: str
    kind: str  # e.g. "rescue_team", "vehicle"


@dataclass(frozen=True)
class Job:
    id: str
    weight: int  # priority weight, >= 1
    needs: tuple[tuple[str, int], ...]  # ((kind, count), ...); the job needs ALL of these


@dataclass
class ResourceAssignment:
    assignments: list[tuple[str, str]]  # (job id, unit id), sorted
    served: list[str]
    unserved: list[str]
    status: str  # OPTIMAL, FEASIBLE, or GREEDY_FALLBACK
    objective: int | None = None
    solve_seconds: float = field(default=0.0, compare=False)


def _validate_resources(units, jobs, cost):
    for label, items in (("unit", units), ("job", jobs)):
        ids = [i.id for i in items]
        if len(set(ids)) != len(ids):
            raise ValueError(f"Duplicate {label} id")
    for job in jobs:
        if job.weight < 1:
            raise ValueError("job weight must be at least 1")
        if not job.needs or any(count < 1 for _, count in job.needs):
            raise ValueError("a job needs at least one unit of each listed kind")
        if len({kind for kind, _ in job.needs}) != len(job.needs):
            raise ValueError("a job may list each kind only once")
    if any(c < 0 for c in cost.values()):
        raise ValueError("costs must be non-negative")


def assign_resources(
    units: list[Unit],
    jobs: list[Job],
    cost: dict[tuple[str, str], int],
    *,
    time_limit_seconds: float = 2.0,
    seed: int = 0,
) -> ResourceAssignment:
    """Match units to jobs: serve the most valuable jobs fully, then minimise travel cost.

    A job is served only if it receives every unit it needs (half a rescue crew is not a crew).
    Each unit serves at most one job. `cost[(unit id, job id)]` is the integer cost of sending that
    unit to that job; a missing pair means the unit cannot safely get there, so it is never used.
    Serving jobs by weight always comes before cheaper journeys (two weight-3 jobs beat one
    weight-5 job: weight is a value, not a strict rank).
    """
    _validate_resources(units, jobs, cost)
    units = sorted(units, key=lambda u: u.id)
    jobs = sorted(jobs, key=lambda j: j.id)
    started = time.perf_counter()

    model = cp_model.CpModel()
    pick: dict[tuple[str, str], object] = {}
    served = {}
    for job in jobs:
        served[job.id] = model.new_bool_var(f"served_{job.id}")
        for kind, count in job.needs:
            crew = []
            for unit in units:
                if unit.kind == kind and (unit.id, job.id) in cost:
                    pick[unit.id, job.id] = model.new_bool_var(f"a_{unit.id}_{job.id}")
                    crew.append(pick[unit.id, job.id])
            # all of the job's requirement for this kind, or none of it
            model.add(sum(crew) == count * served[job.id])
    for unit in units:
        mine = [var for (u_id, _), var in pick.items() if u_id == unit.id]
        if mine:
            model.add(sum(mine) <= 1)

    rank = {u.id: i for i, u in enumerate(units)}
    model.maximize(
        sum(job.weight * WEIGHT_SCALE * served[job.id] for job in jobs)
        - sum(
            (COST_SCALE * cost[u_id, j_id] + rank[u_id]) * var for (u_id, j_id), var in pick.items()
        )
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = seed
    status = solver.solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        chosen = sorted((j_id, u_id) for (u_id, j_id), var in pick.items() if solver.value(var))
        result = _resource_result(jobs, chosen, solver.status_name(status))
        result.objective = int(solver.objective_value)
    else:
        result = _greedy_resources(units, jobs, cost)
    result.solve_seconds = time.perf_counter() - started
    _check_resources(result, units, jobs)
    return result


def _resource_result(jobs, chosen, status) -> ResourceAssignment:
    done = {j_id for j_id, _ in chosen}
    served = [j.id for j in jobs if j.id in done]
    unserved = [j.id for j in jobs if j.id not in done]
    return ResourceAssignment(chosen, served, unserved, status)


def _greedy_resources(units, jobs, cost) -> ResourceAssignment:
    """Deterministic fallback: heaviest jobs first, each taking its cheapest reachable units."""
    free = {u.id: u for u in units}
    chosen = []
    for job in sorted(jobs, key=lambda j: (-j.weight, j.id)):
        crew = []
        for kind, count in job.needs:
            options = sorted(
                (cost[u.id, job.id], u.id)
                for u in free.values()
                if u.kind == kind and (u.id, job.id) in cost
            )
            if len(options) < count:
                crew = None
                break
            crew.extend(u_id for _, u_id in options[:count])
        if crew:
            for u_id in crew:
                chosen.append((job.id, u_id))
                del free[u_id]
    return _resource_result(jobs, sorted(chosen), "GREEDY_FALLBACK")


def _check_resources(result: ResourceAssignment, units, jobs) -> None:
    """Independent re-check: each unit used once, and every served job has exactly its crew."""
    kind = {u.id: u.kind for u in units}
    used = [u_id for _, u_id in result.assignments]
    if len(used) != len(set(used)):
        raise AssertionError("a unit was assigned to more than one job")
    crews: dict[str, dict[str, int]] = {}
    for j_id, u_id in result.assignments:
        crews.setdefault(j_id, {})
        crews[j_id][kind[u_id]] = crews[j_id].get(kind[u_id], 0) + 1
    needs = {j.id: dict(j.needs) for j in jobs}
    if any(crews[j_id] != needs[j_id] for j_id in crews):
        raise AssertionError("a job was given a crew that is not exactly what it needs")
