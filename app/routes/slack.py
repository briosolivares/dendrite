import hashlib
import hmac
import time

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import ValidationError

from app.models import SlackEvent
from app.config import get_settings
from app.service import process_slack_event

router = APIRouter()
MAX_SLACK_TIMESTAMP_AGE_SECONDS = 60 * 5


def _verify_slack_signature(raw_body: bytes, timestamp: str, signature: str) -> None:
    if not timestamp or not signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Slack signature headers",
        )

    try:
        request_time = int(timestamp)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Slack timestamp header",
        ) from exc

    if abs(int(time.time()) - request_time) > MAX_SLACK_TIMESTAMP_AGE_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Slack request timestamp is too old",
        )

    settings = get_settings()
    base_string = f"v0:{timestamp}:{raw_body.decode('utf-8')}"
    expected_signature = "v0=" + hmac.new(
        settings.slack_signing_secret.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Slack request signature",
        )


@router.post("/events")
async def ingest_slack_event(request: Request) -> dict:
    raw_body = await request.body()
    signature = request.headers.get("X-Slack-Signature", "")
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    _verify_slack_signature(raw_body=raw_body, timestamp=timestamp, signature=signature)

    payload = await request.json()

    if payload.get("type") == "url_verification":
        challenge = payload.get("challenge")
        if not challenge:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Slack url_verification payload missing challenge",
            )
        return {"challenge": challenge}

    event_payload = payload.get("event", {})
    try:
        event = SlackEvent.model_validate(event_payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid Slack event payload: {exc}",
        ) from exc

    parsed = process_slack_event(event)
    return {"ok": True, "parsed": parsed.model_dump()}
