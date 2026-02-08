import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from neo4j import Driver
from neo4j import ManagedTransaction
from pydantic import ValidationError

from app.config import get_settings, load_projects_config
from app.models import BootstrapResponse, ParsedMessage, ProposedGraphDiff, SlackEvent
from app.parser import parse_constraint_update, parse_dependency_update, parse_event

logger = logging.getLogger(__name__)
COMMIT_APPLY_LOCK = asyncio.Lock()


def process_slack_event(
    event: SlackEvent,
    *,
    source_message_id: str | None = None,
    source_permalink: str | None = None,
) -> ParsedMessage:
    parsed = parse_event(event)
    if not is_structured_attempt(event.text):
        return parsed

    message_id = source_message_id or f"{event.channel}:{event.ts}"
    permalink = source_permalink or _fallback_permalink(event.channel, event.ts)
    try:
        if "depends_on:" in event.text.lower():
            proposed_diff = parse_dependency_update(
                raw_text=event.text,
                actor_user_id=event.user,
                source_message_id=message_id,
                source_permalink=permalink,
            )
        else:
            proposed_diff = parse_constraint_update(
                raw_text=event.text,
                actor_user_id=event.user,
                source_message_id=message_id,
                source_permalink=permalink,
            )
    except ValueError as exc:
        parsed.parse_error = str(exc)
        return parsed

    parsed.proposed_diff = proposed_diff
    return parsed


def is_structured_attempt(raw_text: str) -> bool:
    normalized = raw_text.lower()
    has_project = "project:" in normalized
    has_mutation = "constraint:" in normalized or "depends_on:" in normalized
    return has_project and has_mutation


def send_thread_feedback_stub(channel_id: str, thread_ts: str, text: str) -> None:
    logger.info(
        "thread_feedback_stub channel_id=%s thread_ts=%s text=%s",
        channel_id,
        thread_ts,
        text,
    )


def _build_commit_message(proposed_diff: ProposedGraphDiff) -> str:
    if proposed_diff.constraint is not None:
        return (
            "ConstraintUpsert "
            f"project={proposed_diff.constraint.project_id} "
            f"key={proposed_diff.constraint.constraint_key} "
            f"why={proposed_diff.reason}"
        )
    if proposed_diff.dependency is not None:
        return (
            "DependencyAdd "
            f"from={proposed_diff.dependency.from_project_id} "
            f"to={proposed_diff.dependency.to_project_id} "
            f"why={proposed_diff.reason}"
        )
    return f"{proposed_diff.update_type} why={proposed_diff.reason}"


def _create_graph_commit_tx(
    tx: ManagedTransaction,
    *,
    commit_id: str,
    actor_user_id: str,
    timestamp: str,
    source: str,
    diff_json: str,
    why: str,
    commit_message: str,
    proposed_diff_data: dict,
) -> dict:
    latest = tx.run(
        """
        MATCH (gc:GraphCommit)
        RETURN gc.commit_id AS commit_id, gc.sequence_number AS sequence_number
        ORDER BY gc.sequence_number DESC
        LIMIT 1
        """
    ).single()

    if latest is None:
        sequence_number = 1
        parent_commit_id = None
    else:
        sequence_number = int(latest["sequence_number"]) + 1
        parent_commit_id = latest["commit_id"]

    tx.run(
        """
        CREATE (gc:GraphCommit {
            commit_id: $commit_id,
            sequence_number: $sequence_number,
            parent_commit_id: $parent_commit_id,
            actor_user_id: $actor_user_id,
            timestamp: $timestamp,
            source: $source,
            diff_json: $diff_json,
            why: $why,
            commit_message: $commit_message
        })
        """,
        commit_id=commit_id,
        sequence_number=sequence_number,
        parent_commit_id=parent_commit_id,
        actor_user_id=actor_user_id,
        timestamp=timestamp,
        source=source,
        diff_json=diff_json,
        why=why,
        commit_message=commit_message,
    ).consume()

    tx.run(
        """
        MATCH (gc:GraphCommit {commit_id: $commit_id})
        OPTIONAL MATCH (m:SlackMessage {message_id: $source_message_id})
        FOREACH (_ IN CASE WHEN m IS NULL THEN [] ELSE [1] END |
            MERGE (gc)-[:FROM_MESSAGE]->(m)
        )
        """,
        commit_id=commit_id,
        source_message_id=proposed_diff_data["source_message_id"],
    ).consume()

    prior_active_constraint_values: list[str] = []
    mutated_project_ids: list[str] = []
    if proposed_diff_data["update_type"] == "ConstraintUpsert":
        constraint = proposed_diff_data["constraint"]
        result = tx.run(
            """
            MATCH (p:Project {project_id: $project_id})
            OPTIONAL MATCH (p)-[:HAS_CONSTRAINT]->(prior:Constraint {
                key: $constraint_key,
                is_active: true
            })
            WITH p, collect(prior) AS prior_constraints
            WITH p,
                 [c IN prior_constraints WHERE c IS NOT NULL | {
                     value: c.value,
                     author_user_id: c.author_user_id
                 }] AS prior_constraints_data,
                 prior_constraints
            FOREACH (c IN prior_constraints |
                SET c.is_active = false, c.deactivated_at = $timestamp
            )
            CREATE (new_constraint:Constraint {
                constraint_id: $constraint_id,
                project_id: $project_id,
                key: $constraint_key,
                value: $constraint_value,
                type: $constraint_type,
                reason: $constraint_reason,
                is_active: true,
                source_message_id: $source_message_id,
                source_permalink: $source_permalink,
                author_user_id: $actor_user_id,
                created_at: $timestamp
            })
            CREATE (p)-[:HAS_CONSTRAINT]->(new_constraint)
            WITH p, new_constraint, prior_constraints_data
            MATCH (gc:GraphCommit {commit_id: $commit_id})
            CREATE (new_constraint)-[:INTRODUCED_BY]->(gc)
            MERGE (gc)-[:APPLIES_TO]->(p)
            SET p.updated_at = $timestamp
            RETURN p.project_id AS project_id, prior_constraints_data AS prior_constraints_data
            """,
            commit_id=commit_id,
            constraint_id=str(uuid.uuid4()),
            project_id=constraint["project_id"],
            constraint_key=constraint["constraint_key"],
            constraint_value=constraint["constraint_value"],
            constraint_type=constraint["constraint_type"],
            constraint_reason=constraint["reason"],
            source_message_id=proposed_diff_data["source_message_id"],
            source_permalink=proposed_diff_data["source_permalink"],
            actor_user_id=actor_user_id,
            timestamp=timestamp,
        ).single()
        if result is None:
            raise ValueError("ConstraintUpsert failed: target project not found")
        prior_constraints_data = result["prior_constraints_data"] or []
        prior_active_constraint_values = [entry["value"] for entry in prior_constraints_data]
        mutated_project_ids = [result["project_id"]]

    if proposed_diff_data["update_type"] == "DependencyAdd":
        dependency = proposed_diff_data["dependency"]
        result = tx.run(
            """
            MATCH (from_p:Project {project_id: $from_project_id})
            MATCH (to_p:Project {project_id: $to_project_id})
            CREATE (from_p)-[:DEPENDS_ON {
                dependency_id: $dependency_id,
                reason: $dependency_reason,
                is_active: true,
                source_message_id: $source_message_id,
                source_permalink: $source_permalink,
                author_user_id: $actor_user_id,
                created_at: $timestamp
            }]->(to_p)
            WITH from_p, to_p
            MATCH (gc:GraphCommit {commit_id: $commit_id})
            MERGE (gc)-[:APPLIES_TO]->(from_p)
            MERGE (gc)-[:APPLIES_TO]->(to_p)
            SET from_p.updated_at = $timestamp,
                to_p.updated_at = $timestamp
            RETURN from_p.project_id AS from_project_id, to_p.project_id AS to_project_id
            """,
            commit_id=commit_id,
            dependency_id=str(uuid.uuid4()),
            from_project_id=dependency["from_project_id"],
            to_project_id=dependency["to_project_id"],
            dependency_reason=dependency["reason"],
            source_message_id=proposed_diff_data["source_message_id"],
            source_permalink=proposed_diff_data["source_permalink"],
            actor_user_id=actor_user_id,
            timestamp=timestamp,
        ).single()
        if result is None:
            raise ValueError("DependencyAdd failed: project nodes not found")
        mutated_project_ids = [result["from_project_id"], result["to_project_id"]]

    return {
        "commit_id": commit_id,
        "sequence_number": sequence_number,
        "parent_commit_id": parent_commit_id,
        "actor_user_id": actor_user_id,
        "timestamp": timestamp,
        "source": source,
        "diff_json": diff_json,
        "why": why,
        "commit_message": commit_message,
        "mutated_project_ids": mutated_project_ids,
        "prior_active_constraint_values": prior_active_constraint_values,
        "prior_active_constraints": prior_constraints_data if proposed_diff_data["update_type"] == "ConstraintUpsert" else [],
    }


def create_graph_commit(driver: Driver, proposed_diff: ProposedGraphDiff, source: str = "slack") -> dict:
    commit_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    diff_json = json.dumps(proposed_diff.model_dump(), sort_keys=True)
    commit_message = _build_commit_message(proposed_diff)

    settings = get_settings()
    with driver.session(database=settings.neo4j_database) as session:
        return session.execute_write(
            _create_graph_commit_tx,
            commit_id=commit_id,
            actor_user_id=proposed_diff.actor_user_id,
            timestamp=timestamp,
            source=source,
            diff_json=diff_json,
            why=proposed_diff.reason,
            commit_message=commit_message,
            proposed_diff_data=proposed_diff.model_dump(),
        )


def get_graph_current_truth(driver: Driver) -> dict:
    settings = get_settings()
    with driver.session(database=settings.neo4j_database) as session:
        constraints_result = session.run(
            """
            MATCH (p:Project)-[:HAS_CONSTRAINT]->(c:Constraint {is_active: true})
            RETURN
              p.project_id AS project_id,
              c.constraint_id AS constraint_id,
              c.key AS constraint_key,
              c.value AS constraint_value,
              c.type AS constraint_type,
              c.reason AS reason,
              c.source_permalink AS source_permalink,
              c.author_user_id AS author_user_id,
              c.created_at AS created_at
            ORDER BY p.project_id, c.key
            """
        )
        constraints = [record.data() for record in constraints_result]

        dependencies_result = session.run(
            """
            MATCH (from_p:Project)-[d:DEPENDS_ON {is_active: true}]->(to_p:Project)
            RETURN
              d.dependency_id AS dependency_id,
              from_p.project_id AS from_project_id,
              to_p.project_id AS to_project_id,
              d.reason AS reason,
              d.source_permalink AS source_permalink,
              d.author_user_id AS author_user_id,
              d.created_at AS created_at
            ORDER BY from_p.project_id, to_p.project_id
            """
        )
        dependencies = [record.data() for record in dependencies_result]

    return {
        "constraints": constraints,
        "dependencies": dependencies,
    }


def get_graph_changes_since(driver: Driver, since_iso8601: str) -> dict:
    settings = get_settings()
    with driver.session(database=settings.neo4j_database) as session:
        result = session.run(
            """
            MATCH (gc:GraphCommit)
            WHERE datetime(gc.timestamp) >= datetime($since_iso8601)
            RETURN
              gc.commit_id AS commit_id,
              gc.sequence_number AS sequence_number,
              gc.parent_commit_id AS parent_commit_id,
              gc.actor_user_id AS actor_user_id,
              gc.timestamp AS timestamp,
              gc.source AS source,
              gc.diff_json AS diff_json,
              gc.why AS why,
              gc.commit_message AS commit_message
            ORDER BY gc.sequence_number ASC
            """,
            since_iso8601=since_iso8601,
        )
        commits = [record.data() for record in result]

    return {"since": since_iso8601, "commits": commits}


def get_project_by_id(driver: Driver, project_id: str) -> dict | None:
    settings = get_settings()
    with driver.session(database=settings.neo4j_database) as session:
        record = session.run(
            """
            MATCH (p:Project {project_id: $project_id})
            OPTIONAL MATCH (owner:Person)-[:OWNS]->(p)
            RETURN
              p.project_id AS project_id,
              p.name AS name,
              p.created_at AS created_at,
              p.updated_at AS updated_at,
              [user_id IN collect(DISTINCT owner.user_id) WHERE user_id IS NOT NULL] AS owner_user_ids
            LIMIT 1
            """,
            project_id=project_id,
        ).single()
    if record is None:
        return None
    return record.data()


def get_project_checklist(driver: Driver, project_id: str) -> dict | None:
    settings = get_settings()
    with driver.session(database=settings.neo4j_database) as session:
        project_exists = session.run(
            """
            MATCH (p:Project {project_id: $project_id})
            RETURN p.project_id AS project_id
            LIMIT 1
            """,
            project_id=project_id,
        ).single()
        if project_exists is None:
            return None

        constraints_result = session.run(
            """
            MATCH (p:Project {project_id: $project_id})-[:HAS_CONSTRAINT]->(c:Constraint {is_active: true})
            RETURN
              c.constraint_id AS constraint_id,
              c.key AS constraint_key,
              c.value AS constraint_value,
              c.type AS constraint_type,
              c.reason AS reason,
              c.source_permalink AS source_permalink,
              c.author_user_id AS author_user_id,
              c.created_at AS created_at
            ORDER BY c.type, c.key
            """,
            project_id=project_id,
        )
        constraints_by_type: dict[str, list[dict]] = {}
        for record in constraints_result:
            item = record.data()
            constraint_type = item.get("constraint_type") or "Unspecified"
            constraints_by_type.setdefault(constraint_type, []).append(item)

        dependencies_result = session.run(
            """
            MATCH (from_p:Project {project_id: $project_id})
              -[d:DEPENDS_ON {is_active: true}]->
              (to_p:Project)
            RETURN
              d.dependency_id AS dependency_id,
              from_p.project_id AS from_project_id,
              to_p.project_id AS to_project_id,
              d.reason AS reason,
              d.source_permalink AS source_permalink,
              d.author_user_id AS author_user_id,
              d.created_at AS created_at
            ORDER BY to_p.project_id
            """,
            project_id=project_id,
        )
        dependencies = [record.data() for record in dependencies_result]

    return {
        "project_id": project_id,
        "constraints_by_type": constraints_by_type,
        "dependencies": dependencies,
    }


def get_configured_project_ids() -> list[str]:
    return [project.project_id for project in load_projects_config().projects]


def find_unknown_project_ids(proposed_diff: ProposedGraphDiff) -> list[str]:
    configured_ids = set(get_configured_project_ids())
    referenced_ids: list[str] = []

    if proposed_diff.constraint is not None:
        referenced_ids.append(proposed_diff.constraint.project_id)
    if proposed_diff.dependency is not None:
        referenced_ids.extend(
            [
                proposed_diff.dependency.from_project_id,
                proposed_diff.dependency.to_project_id,
            ]
        )

    unknown_ids: list[str] = []
    for project_id in referenced_ids:
        if project_id not in configured_ids and project_id not in unknown_ids:
            unknown_ids.append(project_id)
    return unknown_ids


def _fallback_permalink(channel_id: str, message_ts: str) -> str:
    if not channel_id or not message_ts:
        return ""
    return f"https://slack.com/archives/{channel_id}/p{message_ts.replace('.', '')}"


def resolve_source_permalink(channel_id: str, message_ts: str) -> str:
    settings = get_settings()
    query_params = urlencode({"channel": channel_id, "message_ts": message_ts})
    request = Request(
        f"https://slack.com/api/chat.getPermalink?{query_params}",
        headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("ok") and payload.get("permalink"):
            return payload["permalink"]
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        return _fallback_permalink(channel_id, message_ts)
    return _fallback_permalink(channel_id, message_ts)


def _persist_slack_message(
    driver: Driver,
    *,
    message_id: str,
    event_id: str | None,
    ts: str,
    channel_id: str,
    user_id: str,
    raw_text: str,
    permalink: str,
    ingestion_status: str,
    error_reason: str | None = None,
) -> None:
    query = """
    MERGE (m:SlackMessage {message_id: $message_id})
      ON CREATE SET m.created_at = datetime().epochMillis
    SET m.event_id = $event_id,
        m.timestamp = $ts,
        m.channel_id = $channel_id,
        m.user_id = $user_id,
        m.raw_text = $raw_text,
        m.permalink = $permalink,
        m.ingestion_status = $ingestion_status,
        m.error_reason = $error_reason
    """

    settings = get_settings()
    with driver.session(database=settings.neo4j_database) as session:
        session.run(
            query,
            message_id=message_id,
            event_id=event_id,
            ts=ts,
            channel_id=channel_id,
            user_id=user_id,
            raw_text=raw_text,
            permalink=permalink,
            ingestion_status=ingestion_status,
            error_reason=error_reason,
        ).consume()


def update_slack_message_status(
    driver: Driver,
    *,
    message_id: str,
    ingestion_status: str,
    error_reason: str | None = None,
) -> None:
    query = """
    MATCH (m:SlackMessage {message_id: $message_id})
    SET m.ingestion_status = $ingestion_status,
        m.error_reason = $error_reason
    """
    settings = get_settings()
    with driver.session(database=settings.neo4j_database) as session:
        session.run(
            query,
            message_id=message_id,
            ingestion_status=ingestion_status,
            error_reason=error_reason,
        ).consume()


def get_slack_message_status(driver: Driver, *, message_id: str) -> str | None:
    query = """
    MATCH (m:SlackMessage {message_id: $message_id})
    RETURN m.ingestion_status AS ingestion_status
    LIMIT 1
    """
    settings = get_settings()
    with driver.session(database=settings.neo4j_database) as session:
        record = session.run(query, message_id=message_id).single()
    if record is None:
        return None
    return record.get("ingestion_status")


def is_constraint_no_op(driver: Driver, proposed_diff: ProposedGraphDiff) -> bool:
    if proposed_diff.constraint is None:
        return False
    query = """
    MATCH (c:Constraint {
      project_id: $project_id,
      key: $constraint_key,
      value: $constraint_value,
      is_active: true
    })
    RETURN c
    LIMIT 1
    """
    settings = get_settings()
    with driver.session(database=settings.neo4j_database) as session:
        record = session.run(
            query,
            project_id=proposed_diff.constraint.project_id,
            constraint_key=proposed_diff.constraint.constraint_key,
            constraint_value=proposed_diff.constraint.constraint_value,
        ).single()
    return record is not None


def is_dependency_no_op(driver: Driver, proposed_diff: ProposedGraphDiff) -> bool:
    if proposed_diff.dependency is None:
        return False
    query = """
    MATCH (:Project {project_id: $from_project_id})
      -[d:DEPENDS_ON {is_active: true}]->
      (:Project {project_id: $to_project_id})
    RETURN d
    LIMIT 1
    """
    settings = get_settings()
    with driver.session(database=settings.neo4j_database) as session:
        record = session.run(
            query,
            from_project_id=proposed_diff.dependency.from_project_id,
            to_project_id=proposed_diff.dependency.to_project_id,
        ).single()
    return record is not None


def preprocess_slack_event(driver: Driver, payload: dict) -> tuple[bool, dict]:
    event_payload = payload.get("event", {})
    if event_payload.get("type") != "message":
        return False, {"ok": True, "status": "ignored", "reason": "unsupported_event_type"}

    projects_config = load_projects_config()
    configured_channel_id = projects_config.slack.channel_id

    event_id = payload.get("event_id")
    channel_id = event_payload.get("channel", "")
    ts = event_payload.get("ts", "")
    user_id = event_payload.get("user", "")
    raw_text = event_payload.get("text", "")
    subtype = event_payload.get("subtype")
    bot_id = event_payload.get("bot_id")
    message_id = event_id or f"{channel_id}:{ts}"
    structured_attempt = is_structured_attempt(raw_text)
    existing_status = get_slack_message_status(driver, message_id=message_id)
    if existing_status in {"processed", "no_op_duplicate"}:
        update_slack_message_status(
            driver,
            message_id=message_id,
            ingestion_status="no_op_duplicate",
            error_reason="message_already_processed",
        )
        return False, {
            "ok": True,
            "status": "no_op_duplicate",
            "reason": "message_already_processed",
        }

    if bot_id or subtype is not None:
        _persist_slack_message(
            driver,
            message_id=message_id,
            event_id=event_id,
            ts=ts,
            channel_id=channel_id,
            user_id=user_id,
            raw_text=raw_text,
            permalink="",
            ingestion_status="ignored",
            error_reason="bot_or_subtype_message",
        )
        return False, {"ok": True, "status": "ignored", "reason": "bot_or_subtype_message"}

    if channel_id != configured_channel_id:
        _persist_slack_message(
            driver,
            message_id=message_id,
            event_id=event_id,
            ts=ts,
            channel_id=channel_id,
            user_id=user_id,
            raw_text=raw_text,
            permalink="",
            ingestion_status="ignored",
            error_reason="unexpected_channel",
        )
        return False, {"ok": True, "status": "ignored", "reason": "unexpected_channel"}

    permalink = resolve_source_permalink(channel_id=channel_id, message_ts=ts)
    try:
        event = SlackEvent.model_validate(event_payload)
    except ValidationError as exc:
        _persist_slack_message(
            driver,
            message_id=message_id,
            event_id=event_id,
            ts=ts,
            channel_id=channel_id,
            user_id=user_id,
            raw_text=raw_text,
            permalink=permalink,
            ingestion_status="error",
            error_reason=f"invalid_event_payload: {exc}",
        )
        return False, {"ok": False, "status": "error", "reason": "invalid_event_payload"}

    _persist_slack_message(
        driver,
        message_id=message_id,
        event_id=event_id,
        ts=ts,
        channel_id=channel_id,
        user_id=user_id,
        raw_text=raw_text,
        permalink=permalink,
        ingestion_status="processed",
    )
    return True, {
        "ok": True,
        "status": "processed",
        "message_id": message_id,
        "event": event.model_dump(),
        "source_permalink": permalink,
        "structured_attempt": structured_attempt,
    }


def bootstrap_from_config(driver: Driver) -> BootstrapResponse:
    projects_config = load_projects_config()
    project_payload = [
        {
            "project_id": project.project_id,
            "name": project.name,
            "owner_user_ids": project.owner_user_ids,
        }
        for project in projects_config.projects
    ]

    owner_link_count = sum(len(project["owner_user_ids"]) for project in project_payload)

    query = """
    UNWIND $projects AS project
    MERGE (p:Project {project_id: project.project_id})
      ON CREATE SET p.created_at = datetime().epochMillis
    SET p.name = project.name, p.updated_at = datetime().epochMillis
    WITH p, project
    UNWIND project.owner_user_ids AS owner_user_id
    MERGE (person:Person {user_id: owner_user_id})
    MERGE (person)-[:OWNS]->(p)
    """

    settings = get_settings()
    with driver.session(database=settings.neo4j_database) as session:
        session.run(query, projects=project_payload).consume()

    return BootstrapResponse(
        ok=True,
        detail="Bootstrap complete. Project/owner graph is synchronized from config.",
        project_count=len(project_payload),
        owner_link_count=owner_link_count,
    )
