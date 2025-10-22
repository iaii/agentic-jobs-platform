from fastapi import APIRouter, status


router = APIRouter()


@router.post(
    "/create",
    status_code=status.HTTP_200_OK,
)
async def create_draft() -> dict[str, str]:
    return {"message": "stub"}
