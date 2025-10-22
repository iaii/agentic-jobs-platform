from fastapi import APIRouter, status


router = APIRouter()


@router.post(
    "/run",
    status_code=status.HTTP_200_OK,
)
async def run_discovery() -> dict[str, str]:
    return {"message": "stub"}
