from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request

from app.service import (
    get_graph_changes_since,
    get_graph_current_truth,
    get_project_by_id,
    get_project_checklist,
)

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


@router.get("/graph/changes")
def read_graph_changes(request: Request, since: str = Query(...)) -> dict:
    try:
        normalized_since = datetime.fromisoformat(since.replace("Z", "+00:00")).isoformat()
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid 'since' parameter. Use ISO-8601 format.",
        ) from exc

    driver = getattr(request.app.state, "neo4j_driver", None)
    if driver is None:
        raise HTTPException(status_code=503, detail="Neo4j driver is not initialized")
    return get_graph_changes_since(driver, normalized_since)


@router.get("/projects/{project_id}")
def read_project(request: Request, project_id: str) -> dict:
    driver = getattr(request.app.state, "neo4j_driver", None)
    if driver is None:
        raise HTTPException(status_code=503, detail="Neo4j driver is not initialized")
    project = get_project_by_id(driver, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/projects/{project_id}/checklist")
def read_project_checklist(request: Request, project_id: str) -> dict:
    driver = getattr(request.app.state, "neo4j_driver", None)
    if driver is None:
        raise HTTPException(status_code=503, detail="Neo4j driver is not initialized")
    checklist = get_project_checklist(driver, project_id)
    if checklist is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return checklist
