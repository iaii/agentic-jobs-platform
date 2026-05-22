from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, status
from pydantic import BaseModel, field_validator

from agentic_jobs.core.enums import TrustVerdict
from agentic_jobs.services.trust.evaluator import evaluate

router = APIRouter()


class EvaluateTrustRequest(BaseModel):
    url: str
    domain_root: str | None = None

    @field_validator("url")
    @classmethod
    def url_must_have_scheme(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return v


class EvaluateTrustResponse(BaseModel):
    domain_root: str
    score: int
    verdict: TrustVerdict
    signals: list[dict[str, str]]


@router.post(
    "/evaluate",
    status_code=status.HTTP_200_OK,
    response_model=EvaluateTrustResponse,
)
async def evaluate_trust(body: EvaluateTrustRequest) -> EvaluateTrustResponse:
    domain_root = body.domain_root or urlparse(body.url).netloc.lower()
    result = await evaluate(body.url, domain_root)
    return EvaluateTrustResponse(
        domain_root=domain_root,
        score=result.score,
        verdict=result.verdict,
        signals=result.signals,
    )
