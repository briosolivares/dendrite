from typing import Literal

from pydantic import BaseModel, model_validator


class SlackEvent(BaseModel):
    channel: str
    user: str
    text: str
    ts: str


class ParsedMessage(BaseModel):
    summary: str
    entities: list[str]
    proposed_diff: "ProposedGraphDiff | None" = None
    parse_error: str | None = None


class ConstraintDiff(BaseModel):
    project_id: str
    constraint_key: str
    constraint_value: str
    constraint_type: Literal["DesignChoice", "Requirement"] = "DesignChoice"
    reason: str


class DependencyDiff(BaseModel):
    from_project_id: str
    to_project_id: str
    reason: str


class ProposedGraphDiff(BaseModel):
    update_type: Literal["ConstraintUpsert", "DependencyAdd"]
    actor_user_id: str
    source_message_id: str
    source_permalink: str
    constraint: ConstraintDiff | None = None
    dependency: DependencyDiff | None = None
    reason: str

    @model_validator(mode="after")
    def validate_required_payload(self) -> "ProposedGraphDiff":
        if self.update_type == "ConstraintUpsert" and self.constraint is None:
            raise ValueError("constraint is required when update_type is ConstraintUpsert")
        if self.update_type == "DependencyAdd" and self.dependency is None:
            raise ValueError("dependency is required when update_type is DependencyAdd")
        return self


class BootstrapResponse(BaseModel):
    ok: bool
    detail: str
    project_count: int = 0
    owner_link_count: int = 0
