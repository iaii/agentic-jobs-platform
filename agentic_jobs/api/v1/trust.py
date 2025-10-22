from fastapi import APIRouter, status


router = APIRouter()


@router.post(
    "/evaluate",
    status_code=status.HTTP_200_OK,
)
async def evaluate_trust() -> dict[str, str]:
    return {"message": "stub"}
