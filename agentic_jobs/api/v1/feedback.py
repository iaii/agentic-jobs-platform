from fastapi import APIRouter, status


router = APIRouter()


@router.post(
    "/feedback",
    status_code=status.HTTP_200_OK,
)
async def drafts_feedback() -> dict[str, str]:
    return {"message": "stub"}
