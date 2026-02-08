from fastapi import APIRouter

from app.models import SlackEvent
from app.service import process_slack_event

router = APIRouter()


@router.post("/event")
def ingest_slack_event(event: SlackEvent) -> dict:
    parsed = process_slack_event(event)
    return {"ok": True, "parsed": parsed.model_dump()}
