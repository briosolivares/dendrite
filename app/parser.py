import re

from app.models import (
    ConstraintDiff,
    DependencyDiff,
    ParsedMessage,
    ProposedGraphDiff,
    SlackEvent,
)

CONSTRAINT_UPDATE_PATTERN = re.compile(
    r"^project:\s*(?P<project_id>\S+)\s+"
    r"constraint:\s*(?P<constraint_key>[^=\s]+)=(?P<constraint_value>\S+)\s+"
    r"(?:(?:type:\s*(?P<constraint_type>DesignChoice|Requirement)\s+))?"
    r"why:\s*(?P<reason>.+?)\s*$",
    re.IGNORECASE,
)
DEPENDENCY_UPDATE_PATTERN = re.compile(
    r"^project:\s*(?P<project_id>\S+)\s+"
    r"depends_on:\s*(?P<depends_on_project_id>\S+)\s+"
    r"why:\s*(?P<reason>.+?)\s*$",
    re.IGNORECASE,
)


def parse_constraint_update(
    *,
    raw_text: str,
    actor_user_id: str,
    source_message_id: str,
    source_permalink: str,
) -> ProposedGraphDiff:
    match = CONSTRAINT_UPDATE_PATTERN.match(raw_text.strip())
    if match is None:
        raise ValueError(
            "Constraint update must match: "
            "project: <project_id> constraint: <key>=<value> "
            "[type: <DesignChoice|Requirement>] why: <reason>"
        )

    groups = match.groupdict()
    constraint_type = groups.get("constraint_type") or "DesignChoice"
    reason = groups["reason"].strip()
    if not reason:
        raise ValueError("Constraint update requires non-empty why: <reason>")

    constraint = ConstraintDiff(
        project_id=groups["project_id"],
        constraint_key=groups["constraint_key"],
        constraint_value=groups["constraint_value"],
        constraint_type=constraint_type,
        reason=reason,
    )
    return ProposedGraphDiff(
        update_type="ConstraintUpsert",
        actor_user_id=actor_user_id,
        source_message_id=source_message_id,
        source_permalink=source_permalink,
        constraint=constraint,
        reason=reason,
    )


def parse_dependency_update(
    *,
    raw_text: str,
    actor_user_id: str,
    source_message_id: str,
    source_permalink: str,
) -> ProposedGraphDiff:
    match = DEPENDENCY_UPDATE_PATTERN.match(raw_text.strip())
    if match is None:
        raise ValueError(
            "Dependency update must match: "
            "project: <project_id> depends_on: <other_project_id> why: <reason>"
        )

    groups = match.groupdict()
    reason = groups["reason"].strip()
    if not reason:
        raise ValueError("Dependency update requires non-empty why: <reason>")

    dependency = DependencyDiff(
        from_project_id=groups["project_id"],
        to_project_id=groups["depends_on_project_id"],
        reason=reason,
    )
    return ProposedGraphDiff(
        update_type="DependencyAdd",
        actor_user_id=actor_user_id,
        source_message_id=source_message_id,
        source_permalink=source_permalink,
        dependency=dependency,
        reason=reason,
    )


def parse_event(event: SlackEvent) -> ParsedMessage:
    entities = [token for token in event.text.split() if token.startswith("#")]
    summary = event.text[:120].strip()
    return ParsedMessage(summary=summary, entities=entities)
