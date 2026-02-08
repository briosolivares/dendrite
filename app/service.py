import json
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from neo4j import Driver
from pydantic import ValidationError

from app.config import get_settings, load_projects_config
from app.models import BootstrapResponse, ParsedMessage, SlackEvent
from app.parser import parse_event


def process_slack_event(event: SlackEvent) -> ParsedMessage:
    return parse_event(event)


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


def preprocess_slack_event(driver: Driver, payload: dict) -> tuple[bool, dict]:
    event_payload = payload.get("event", {})
    if event_payload.get("type") != "message":
        return False, {"ok": True, "status": "ignored", "reason": "unsupported_event_type"}

    event_id = payload.get("event_id")
    channel_id = event_payload.get("channel", "")
    ts = event_payload.get("ts", "")
    user_id = event_payload.get("user", "")
    raw_text = event_payload.get("text", "")
    subtype = event_payload.get("subtype")
    bot_id = event_payload.get("bot_id")
    message_id = event_id or f"{channel_id}:{ts}"

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
