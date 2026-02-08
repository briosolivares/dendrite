from fastapi import APIRouter

from app.models import BootstrapResponse

router = APIRouter()


@router.post("")
def bootstrap() -> BootstrapResponse:
    return BootstrapResponse(ok=True, detail="bootstrap route online")
