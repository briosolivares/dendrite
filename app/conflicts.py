import json
import logging
import uuid
from datetime import datetime, timezone

from neo4j import Driver

from app.config import get_settings, load_projects_config
from app.models import ProposedGraphDiff

logger = logging.getLogger(__name__)


def _detect_constraint_conflict(proposed_diff: ProposedGraphDiff, commit: dict) -> dict | None:
    if proposed_diff.constraint is None:
        return None

    prior_constraints: list[dict] = commit.get("prior_active_constraints", [])
    new_value = proposed_diff.constraint.constraint_value
    differing_prior_values = sorted(
        {entry.get("value") for entry in prior_constraints if entry.get("value") != new_value}
    )
    if not differing_prior_values:
        return None
    prior_conflicting_author_user_ids = sorted(
        {
            entry.get("author_user_id")
            for entry in prior_constraints
            if entry.get("value") != new_value and entry.get("author_user_id")
        }
    )

    return {
        "conflict_type": "constraint_conflict",
        "project_id": proposed_diff.constraint.project_id,
        "constraint_key": proposed_diff.constraint.constraint_key,
        "new_value": new_value,
        "prior_values": differing_prior_values,
        "prior_conflicting_author_user_ids": prior_conflicting_author_user_ids,
        "commit_id": commit.get("commit_id"),
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


def persist_conflict_reports(driver: Driver, commit_id: str, conflicts: list[dict]) -> list[str]:
    if not conflicts:
        return []

    settings = get_settings()
    created_report_ids: list[str] = []
    with driver.session(database=settings.neo4j_database) as session:
        for conflict in conflicts:
            conflict_id = str(uuid.uuid4())
            session.run(
                """
                MATCH (gc:GraphCommit {commit_id: $commit_id})
                CREATE (cr:ConflictReport {
                    conflict_id: $conflict_id,
                    conflict_type: $conflict_type,
                    details_json: $details_json,
                    created_at: $created_at
                })
                CREATE (cr)-[:TRIGGERED_BY]->(gc)
                """,
                commit_id=commit_id,
                conflict_id=conflict_id,
                conflict_type=conflict["conflict_type"],
                details_json=json.dumps(conflict, sort_keys=True),
                created_at=datetime.now(timezone.utc).isoformat(),
            ).consume()
            created_report_ids.append(conflict_id)
    return created_report_ids


def _project_owner_ids_by_project() -> dict[str, list[str]]:
    return {
        project.project_id: project.owner_user_ids
        for project in load_projects_config().projects
    }


def _involved_project_ids(conflict: dict) -> list[str]:
    if conflict["conflict_type"] == "constraint_conflict":
        return [conflict["project_id"]]
    if conflict["conflict_type"] == "dependency_cycle":
        return [conflict["from_project_id"], conflict["to_project_id"]]
    return []


def build_conflict_notification_payload(
    proposed_diff: ProposedGraphDiff, commit: dict, conflicts: list[dict]
) -> dict:
    owners_by_project = _project_owner_ids_by_project()
    recipients: set[str] = {proposed_diff.actor_user_id}

    for conflict in conflicts:
        for project_id in _involved_project_ids(conflict):
            recipients.update(owners_by_project.get(project_id, []))
        for prior_author in conflict.get("prior_conflicting_author_user_ids", []):
            recipients.add(prior_author)

    return {
        "notification_type": "conflict_detected",
        "commit_id": commit.get("commit_id"),
        "sequence_number": commit.get("sequence_number"),
        "actor_user_id": proposed_diff.actor_user_id,
        "recipient_user_ids": sorted(recipients),
        "conflicts": conflicts,
    }


def log_conflict_notification_stub(payload: dict) -> None:
    logger.warning("conflict_notification_stub %s", json.dumps(payload, sort_keys=True))
