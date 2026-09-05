from fastapi import APIRouter

from app.db.session import engine
from app.services.health_service import check_database_health

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    db_ok, db_detail = check_database_health(engine)

    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "unavailable",
        "detail": db_detail,
    }
