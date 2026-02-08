from fastapi import APIRouter, HTTPException, Request

from app.service import get_graph_current_truth

router = APIRouter()


@router.get("/status")
def read_status() -> dict[str, str]:
    return {"message": "read route online"}


@router.get("/graph/current")
def read_graph_current(request: Request) -> dict:
    driver = getattr(request.app.state, "neo4j_driver", None)
    if driver is None:
        raise HTTPException(status_code=503, detail="Neo4j driver is not initialized")
    return get_graph_current_truth(driver)
