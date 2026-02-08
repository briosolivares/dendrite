import hashlib
import hmac
import time

from fastapi import APIRouter, HTTPException, Request, status

from app.config import get_settings
from app.conflicts import (
    build_conflict_notification_payload,
    detect_conflicts_after_commit,
    log_conflict_notification_stub,
    persist_conflict_reports,
)
from app.models import SlackEvent
from app.service import (
    COMMIT_APPLY_LOCK,
    create_graph_commit,
    find_unknown_project_ids,
    get_configured_project_ids,
    is_constraint_no_op,
    is_dependency_no_op,
    preprocess_slack_event,
    process_slack_event,
    send_thread_feedback_stub,
    update_slack_message_status,
)

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

    driver = getattr(request.app.state, "neo4j_driver", None)
    if driver is None:
        raise HTTPException(status_code=503, detail="Neo4j driver is not initialized")

    should_process, result = preprocess_slack_event(driver=driver, payload=payload)
    if not should_process:
        return result

    event = SlackEvent.model_validate(result["event"])
    parsed = process_slack_event(
        event,
        source_message_id=result.get("message_id"),
        source_permalink=result.get("source_permalink"),
    )
    if result.get("structured_attempt") and parsed.parse_error:
        send_thread_feedback_stub(
            channel_id=event.channel,
            thread_ts=event.ts,
            text="Could not parse update. Please follow the pinned template.",
        )
        message_id = result.get("message_id")
        if message_id:
            update_slack_message_status(
                driver,
                message_id=message_id,
                ingestion_status="ignored",
                error_reason="invalid_format",
            )
        return {
            **result,
            "status": "ignored",
            "reason": "invalid_format",
            "parsed": parsed.model_dump(),
        }

    if parsed.proposed_diff is not None:
        unknown_project_ids = find_unknown_project_ids(parsed.proposed_diff)
        if unknown_project_ids:
            valid_projects = ", ".join(get_configured_project_ids())
            send_thread_feedback_stub(
                channel_id=event.channel,
                thread_ts=event.ts,
                text=f"Unknown project_id. Valid projects: {valid_projects}.",
            )
            message_id = result.get("message_id")
            if message_id:
                update_slack_message_status(
                    driver,
                    message_id=message_id,
                    ingestion_status="invalid_unknown_project",
                    error_reason=f"unknown_project_id:{','.join(unknown_project_ids)}",
                )
            return {
                **result,
                "status": "invalid_unknown_project",
                "reason": "unknown_project_id",
                "unknown_project_ids": unknown_project_ids,
                "parsed": parsed.model_dump(),
            }

        message_id = result.get("message_id")
        if is_constraint_no_op(driver, parsed.proposed_diff):
            if message_id:
                update_slack_message_status(
                    driver,
                    message_id=message_id,
                    ingestion_status="no_op_duplicate",
                    error_reason="constraint_no_op_duplicate",
                )
            return {
                **result,
                "status": "no_op_duplicate",
                "reason": "constraint_no_op_duplicate",
                "parsed": parsed.model_dump(),
            }

        if is_dependency_no_op(driver, parsed.proposed_diff):
            if message_id:
                update_slack_message_status(
                    driver,
                    message_id=message_id,
                    ingestion_status="no_op_duplicate",
                    error_reason="dependency_no_op_duplicate",
                )
            return {
                **result,
                "status": "no_op_duplicate",
                "reason": "dependency_no_op_duplicate",
                "parsed": parsed.model_dump(),
            }

        async with COMMIT_APPLY_LOCK:
            commit = create_graph_commit(driver, parsed.proposed_diff, source="slack")
        conflicts = detect_conflicts_after_commit(driver, parsed.proposed_diff, commit)
        conflict_report_ids: list[str] = []
        if conflicts:
            conflict_report_ids = persist_conflict_reports(
                driver, commit_id=commit["commit_id"], conflicts=conflicts
            )
            notification_payload = build_conflict_notification_payload(
                parsed.proposed_diff, commit, conflicts
            )
            log_conflict_notification_stub(notification_payload)
        else:
            project_id = ""
            if parsed.proposed_diff.constraint is not None:
                project_id = parsed.proposed_diff.constraint.project_id
            elif parsed.proposed_diff.dependency is not None:
                project_id = parsed.proposed_diff.dependency.from_project_id

            send_thread_feedback_stub(
                channel_id=event.channel,
                thread_ts=event.ts,
                text=(
                    f"Committed: {commit['commit_id']} | "
                    f"project: {project_id} | "
                    f"summary: {commit['commit_message']}"
                ),
            )

        return {
            **result,
            "parsed": parsed.model_dump(),
            "commit": commit,
            "conflicts": conflicts,
            "conflict_report_ids": conflict_report_ids,
        }

    return {**result, "parsed": parsed.model_dump()}
