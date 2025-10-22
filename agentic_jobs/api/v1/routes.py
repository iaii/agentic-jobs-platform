from fastapi import APIRouter

from agentic_jobs.api.v1 import applications, discover, drafts, feedback, trust


router = APIRouter()
router.include_router(trust.router, prefix="/trust", tags=["trust"])
router.include_router(applications.router, prefix="/applications", tags=["applications"])
router.include_router(discover.router, prefix="/discover", tags=["discover"])
router.include_router(drafts.router, prefix="/drafts", tags=["drafts"])
router.include_router(feedback.router, prefix="/drafts", tags=["drafts"])
