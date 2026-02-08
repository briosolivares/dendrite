import json
import logging
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from neo4j import Driver
from pydantic import ValidationError

from app.config import get_settings, load_projects_config
from app.models import BootstrapResponse, ParsedMessage, ProposedGraphDiff, SlackEvent
from app.parser import parse_constraint_update, parse_dependency_update, parse_event

logger = logging.getLogger(__name__)


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
