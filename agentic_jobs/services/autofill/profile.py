from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_jobs.config import Settings, settings
from agentic_jobs.db import models


LOGGER = logging.getLogger(__name__)


class ProfileLoadError(RuntimeError):
    """Raised when an autofill profile cannot be constructed."""


@dataclass(slots=True)
class ProfileAddress:
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None


@dataclass(slots=True)
class IdentitySnapshot:
    full_name: str
    preferred_name: str | None
    email: str | None
    phone: str | None
    base_location: str | None
    address: ProfileAddress | None


@dataclass(slots=True)
class ProfileFilesSnapshot:
    resume_variants: Dict[str, Path]
    default_resume_tag: str | None
    cover_letter_pdf_enabled: bool
    cover_letter_pdf_path: Path | None
    resume_text_path: Path | None = None  # plain-text resume for LLM experience fields

    def select_resume(self, tag: str | None = None) -> Path | None:
        if not self.resume_variants:
            return None
        target = tag or self.default_resume_tag
        if target and target in self.resume_variants:
            return self.resume_variants[target]
        # Fall back to first available file
        return next(iter(self.resume_variants.values()))


@dataclass(slots=True)
class AutofillProfile:
    identity: IdentitySnapshot
    links: dict[str, str]
    facts: dict[str, Any]
    compliance: dict[str, Any]
    files: ProfileFilesSnapshot
    quick_answers: dict[str, Any]


class ProfileLoader:
    def __init__(self, config: Settings | None = None) -> None:
        self.settings = config or settings

    def load(self, session: Session | None = None) -> AutofillProfile:
        profile = None
        if session is not None:
            profile = self._load_from_db(session)
        if profile is None:
            profile = self._load_from_file()
        if profile is None:
            raise ProfileLoadError("No profile data available for autofill.")
        return profile

    def _load_from_db(self, session: Session) -> AutofillProfile | None:
        rows = session.execute(
            select(models.ProfileIdentity).order_by(models.ProfileIdentity.id)
        ).scalars().all()
        if not rows:
            return None
        if len(rows) > 1:
            LOGGER.warning(
                "Multiple ProfileIdentity rows found (%d); using the first by id. "
                "This system is single-user — remove extra rows to suppress this warning.",
                len(rows),
            )
        identity = rows[0]
        address = ProfileAddress()
        snapshot = IdentitySnapshot(
            full_name=identity.name,
            preferred_name=identity.preferred_name,
            email=identity.email,
            phone=identity.phone,
            base_location=identity.base_location,
            address=address,
        )
        links = {}
        if identity.links:
            if identity.links.linkedin:
                links["linkedin"] = identity.links.linkedin
            if identity.links.github:
                links["github"] = identity.links.github
            if identity.links.portfolio:
                links["portfolio"] = identity.links.portfolio
        facts = {}
        if identity.facts:
            facts = {
                "skills": identity.facts.skills,
                "tools": identity.facts.tools,
                "frameworks": identity.facts.frameworks,
                "projects": identity.facts.projects,
                "education": identity.facts.education,
                "work_auth": identity.facts.work_auth,
            }
        files = self._build_files_from_db(
            identity.files, self.settings.autofill_cl_pdf_enabled
        )
        return AutofillProfile(
            identity=snapshot,
            links=links,
            facts=facts,
            compliance={},
            files=files,
            quick_answers={},
        )

    def _load_from_file(self) -> AutofillProfile | None:
        profile_path = Path(self.settings.autofill_fake_profile_path)
        if not profile_path.exists():
            LOGGER.warning("Fake profile file %s not found.", profile_path)
            return None
        with profile_path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
        identity_raw = payload.get("identity") or {}
        address = ProfileAddress(**(identity_raw.get("address") or {})) if identity_raw.get("address") else None
        snapshot = IdentitySnapshot(
            full_name=identity_raw.get("full_name") or "Unknown Candidate",
            preferred_name=identity_raw.get("preferred_name"),
            email=identity_raw.get("email"),
            phone=identity_raw.get("phone"),
            base_location=identity_raw.get("base_location"),
            address=address,
        )
        links = payload.get("links") or {}
        facts = payload.get("facts") or {}
        compliance = payload.get("compliance") or {}
        quick_answers = payload.get("quick_answers") or {}
        files = self._build_files_from_payload(payload.get("files") or {})
        return AutofillProfile(
            identity=snapshot,
            links=links,
            facts=facts,
            compliance=compliance,
            files=files,
            quick_answers=quick_answers,
        )

    @staticmethod
    def _build_files_from_db(
        file_entry: models.ProfileFiles | None, cover_letter_pdf_enabled: bool
    ) -> ProfileFilesSnapshot:
        resume_variants: Dict[str, Path] = {}
        default_tag: str | None = None
        cover_letter_path: Path | None = None
        resume_text_path: Path | None = None
        if file_entry:
            for variant in file_entry.resume_variants or []:
                tag = variant.get("tag") or variant.get("name")
                path = variant.get("path")
                if tag and path:
                    resume_variants[tag] = Path(path)
            default_tag = file_entry.resume_variants[0].get("tag") if file_entry.resume_variants else None
            if file_entry.resume_text_path:
                resume_text_path = Path(file_entry.resume_text_path)
        return ProfileFilesSnapshot(
            resume_variants=resume_variants,
            default_resume_tag=default_tag,
            cover_letter_pdf_enabled=cover_letter_pdf_enabled,
            cover_letter_pdf_path=cover_letter_path,
            resume_text_path=resume_text_path,
        )

    @staticmethod
    def _build_files_from_payload(payload: dict[str, Any]) -> ProfileFilesSnapshot:
        resume_variants: Dict[str, Path] = {}
        for variant in payload.get("resume_variants") or []:
            tag = variant.get("tag")
            path = variant.get("path")
            if tag and path:
                resume_variants[tag] = Path(path)
        default_tag = payload.get("default_resume_tag")
        cover_letter_cfg = payload.get("cover_letter_pdf") or {}
        cover_letter_enabled = bool(cover_letter_cfg.get("enabled"))
        cover_letter_path = Path(cover_letter_cfg.get("path")) if cover_letter_cfg.get("path") else None
        resume_text_path_str = payload.get("resume_text_path")
        resume_text_path = Path(resume_text_path_str) if resume_text_path_str else None
        return ProfileFilesSnapshot(
            resume_variants=resume_variants,
            default_resume_tag=default_tag,
            cover_letter_pdf_enabled=cover_letter_enabled,
            cover_letter_pdf_path=cover_letter_path,
            resume_text_path=resume_text_path,
        )
