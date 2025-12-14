from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from agentic_jobs.db.session import get_session
from agentic_jobs.services.drafts.generator import DraftGenerator, DraftGeneratorError, DraftResult


router = APIRouter()


class DraftRequest(BaseModel):
    application_id: UUID = Field(..., description="Application identifier")
    notes: list[str] = Field(default_factory=list)
    author: str | None = Field(None, description="Optional human readable author identifier")


class DraftResponse(BaseModel):
    application_id: UUID
    human_id: str
    version: str
    cover_letter_md: str
    artifact_uri: str

    @classmethod
    def from_result(cls, result: DraftResult) -> "DraftResponse":
        return cls(
            application_id=result.application_id,
            human_id=result.human_id,
            version=result.version,
            cover_letter_md=result.cover_letter_md,
            artifact_uri=result.artifact_uri,
        )


def get_draft_generator(session=Depends(get_session)) -> DraftGenerator:
    return DraftGenerator(session)


@router.post(
    "/create",
    status_code=status.HTTP_200_OK,
    response_model=DraftResponse,
)
async def create_draft(
    request: DraftRequest,
    generator: DraftGenerator = Depends(get_draft_generator),
) -> DraftResponse:
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
