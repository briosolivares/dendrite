from fastapi import FastAPI

from app.config import get_settings, validate_runtime_config
from app.routes import bootstrap, read, slack

settings = get_settings()
app = FastAPI(title=settings.app_name)

app.include_router(slack.router, prefix="/slack", tags=["slack"])
app.include_router(read.router, prefix="/read", tags=["read"])
app.include_router(bootstrap.router, prefix="/bootstrap", tags=["bootstrap"])


@app.on_event("startup")
def validate_config_on_startup() -> None:
    validate_runtime_config()


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
