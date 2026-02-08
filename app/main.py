from fastapi import FastAPI, Request

from app.config import get_settings, validate_runtime_config
from app.neo4j_client import check_database_health, get_driver, verify_driver_connectivity
from app.routes import bootstrap, read, slack

settings = get_settings()
app = FastAPI(title=settings.app_name)

app.include_router(slack.router, prefix="/slack", tags=["slack"])
app.include_router(read.router, prefix="/read", tags=["read"])
app.include_router(bootstrap.router, prefix="/bootstrap", tags=["bootstrap"])


@app.on_event("startup")
def startup() -> None:
    validate_runtime_config()
    driver = get_driver()
    verify_driver_connectivity(driver)
    app.state.neo4j_driver = driver


@app.on_event("shutdown")
def shutdown() -> None:
    driver = getattr(app.state, "neo4j_driver", None)
    if driver is not None:
        driver.close()


@app.get("/health", tags=["health"])
def health(request: Request) -> dict:
    driver = getattr(request.app.state, "neo4j_driver", None)
    if driver is None:
        return {
            "app": {"status": "ok"},
            "database": {"status": "not_initialized", "database": settings.neo4j_database},
        }

    db_ok, detail = check_database_health(driver)
    return {
        "app": {"status": "ok"},
        "database": {
            "status": "ok" if db_ok else "error",
            "database": settings.neo4j_database,
            "detail": detail,
        },
    }
