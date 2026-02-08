from fastapi import APIRouter

router = APIRouter()


@router.get("/status")
def read_status() -> dict[str, str]:
    return {"message": "read route online"}
