from fastapi import FastAPI

from app.config import settings
from app.routes import bootstrap, read, slack

app = FastAPI(title=settings.app_name)

app.include_router(slack.router, prefix="/slack", tags=["slack"])
app.include_router(read.router, prefix="/read", tags=["read"])
app.include_router(bootstrap.router, prefix="/bootstrap", tags=["bootstrap"])


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
