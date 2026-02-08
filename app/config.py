import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

load_dotenv()


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    app_name: str
    environment: str
    neo4j_uri: str
    neo4j_username: str
    neo4j_password: str
    neo4j_database: str
    slack_bot_token: str
    slack_signing_secret: str


class SlackChannelConfig(BaseModel):
    channel_name: str
    channel_id: str

    @field_validator("channel_name", "channel_id")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()


class ProjectConfig(BaseModel):
    project_id: str
    name: str
    owner_user_ids: list[str] = Field(min_length=1)

    @field_validator("project_id", "name")
    @classmethod
    def required_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()

    @field_validator("owner_user_ids")
    @classmethod
    def owner_user_ids_must_be_non_blank(cls, value: list[str]) -> list[str]:
        cleaned = [owner.strip() for owner in value if owner.strip()]
        if len(cleaned) != len(value):
            raise ValueError("owner_user_ids must not contain empty values")
        return cleaned


class ProjectsConfig(BaseModel):
    slack: SlackChannelConfig
    projects: list[ProjectConfig] = Field(min_length=2)

    @model_validator(mode="after")
    def unique_project_ids(self) -> "ProjectsConfig":
        project_ids = [project.project_id for project in self.projects]
        if len(project_ids) != len(set(project_ids)):
            raise ValueError("projects.project_id values must be unique")
        return self


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings(
            app_name=os.getenv("APP_NAME", "dendrite-api").strip() or "dendrite-api",
            environment=os.getenv("ENVIRONMENT", "development").strip() or "development",
            neo4j_uri=_required_env("NEO4J_URI"),
            neo4j_username=_required_env("NEO4J_USERNAME"),
            neo4j_password=_required_env("NEO4J_PASSWORD"),
            neo4j_database=_required_env("NEO4J_DATABASE"),
            slack_bot_token=_required_env("SLACK_BOT_TOKEN"),
            slack_signing_secret=_required_env("SLACK_SIGNING_SECRET"),
        )
    except ConfigError as exc:
        raise ConfigError(f"Invalid runtime environment configuration: {exc}") from exc


@lru_cache
def load_projects_config(path: str = "config/projects.json") -> ProjectsConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Missing required project config file: {config_path}")

    try:
        raw_data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"Invalid JSON in {config_path}: line {exc.lineno}, column {exc.colno}"
        ) from exc

    try:
        return ProjectsConfig.model_validate(raw_data)
    except ValidationError as exc:
        raise ConfigError(
            f"Invalid project config in {config_path}: {exc}"
        ) from exc


def validate_runtime_config() -> None:
    get_settings()
    load_projects_config()
