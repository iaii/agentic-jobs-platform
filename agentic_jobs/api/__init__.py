from fastapi import APIRouter

from agentic_jobs.api.v1.routes import router as v1_router


api_router = APIRouter()
api_router.include_router(v1_router, prefix="/api/v1")

__all__ = ["api_router"]
