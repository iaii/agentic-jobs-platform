from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from agentic_jobs.services.drafts.generator import DraftGenerator, DraftGeneratorError
from agentic_jobs.api.v1.drafts import DraftResponse, get_draft_generator


router = APIRouter()


class FeedbackRequest(BaseModel):
    application_id: UUID
    notes: list[str] = Field(default_factory=list)
    author: str | None = None


@router.post(
    "/feedback",
    status_code=status.HTTP_200_OK,
    response_model=DraftResponse,
)
async def drafts_feedback(
    request: FeedbackRequest,
    generator: DraftGenerator = Depends(get_draft_generator),
) -> DraftResponse:
    if not request.notes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Notes are required for feedback.")
    try:
        result = await generator.generate(
            request.application_id,
            notes=request.notes,
            author=request.author,
            post_to_slack=False,
        )
        return DraftResponse.from_result(result)
    except DraftGeneratorError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
