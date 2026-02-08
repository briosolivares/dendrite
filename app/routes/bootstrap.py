from fastapi import APIRouter, HTTPException, Request

from app.models import BootstrapResponse
from app.service import bootstrap_from_config

router = APIRouter()


@router.post("")
def bootstrap(request: Request) -> BootstrapResponse:
    driver = getattr(request.app.state, "neo4j_driver", None)
    if driver is None:
        raise HTTPException(status_code=503, detail="Neo4j driver is not initialized")
    return bootstrap_from_config(driver)
