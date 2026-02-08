from neo4j import Driver

from app.config import get_settings
from app.models import ProposedGraphDiff


def _detect_constraint_conflict(proposed_diff: ProposedGraphDiff, commit: dict) -> dict | None:
    if proposed_diff.constraint is None:
        return None

    prior_values: list[str] = commit.get("prior_active_constraint_values", [])
    new_value = proposed_diff.constraint.constraint_value
    differing_prior_values = sorted({value for value in prior_values if value != new_value})
    if not differing_prior_values:
        return None

    return {
        "conflict_type": "constraint_conflict",
        "project_id": proposed_diff.constraint.project_id,
        "constraint_key": proposed_diff.constraint.constraint_key,
        "new_value": new_value,
        "prior_values": differing_prior_values,
    }


def _detect_dependency_cycle(driver: Driver, proposed_diff: ProposedGraphDiff, commit: dict) -> dict | None:
    if proposed_diff.dependency is None:
        return None

    from_project_id = proposed_diff.dependency.from_project_id
    to_project_id = proposed_diff.dependency.to_project_id
    query = """
    MATCH p = shortestPath(
      (start:Project {project_id: $start_project_id})
      -[:DEPENDS_ON {is_active: true}*1..]->
      (target:Project {project_id: $target_project_id})
    )
    RETURN [node IN nodes(p) | node.project_id] AS path_ids
    LIMIT 1
    """
    settings = get_settings()
    with driver.session(database=settings.neo4j_database) as session:
        record = session.run(
            query,
            start_project_id=to_project_id,
            target_project_id=from_project_id,
        ).single()
    if record is None:
        return None

    path_ids = record["path_ids"] or []
    if not path_ids:
        return None

    return {
        "conflict_type": "dependency_cycle",
        "from_project_id": from_project_id,
        "to_project_id": to_project_id,
        "cycle_path": [from_project_id, *path_ids],
        "commit_id": commit.get("commit_id"),
    }


def detect_conflicts_after_commit(
    driver: Driver, proposed_diff: ProposedGraphDiff, commit: dict
) -> list[dict]:
    conflicts: list[dict] = []

    constraint_conflict = _detect_constraint_conflict(proposed_diff, commit)
    if constraint_conflict is not None:
        conflicts.append(constraint_conflict)

    dependency_cycle = _detect_dependency_cycle(driver, proposed_diff, commit)
    if dependency_cycle is not None:
        conflicts.append(dependency_cycle)

    return conflicts
