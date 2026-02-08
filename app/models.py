from pydantic import BaseModel


class SlackEvent(BaseModel):
    channel: str
    user: str
    text: str
    ts: str


class ParsedMessage(BaseModel):
    summary: str
    entities: list[str]


class BootstrapResponse(BaseModel):
    ok: bool
    detail: str
