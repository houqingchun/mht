from fastapi import APIRouter

from app.core.errors import ok

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return ok({"status": "ok"})

