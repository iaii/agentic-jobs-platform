from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping

import yaml
import hashlib


KIT_PATH = Path(__file__).resolve().parents[2] / "profile" / "cover_letter_kit.yaml"


@dataclass(slots=True)
class ProjectCard:
    key: str
    name: str
    short_name: str
    summary: str
    talking_points: list[str]
    themes: list[str]


@dataclass(slots=True)
class ProfileSnapshot:
    bio: str
    background: list[str]
    technical_strengths: dict[str, list[str]]
    work_style: list[str]


@dataclass(slots=True)
class SkillsCard:
    languages: list[str]
    backend: list[str]
    frontend: list[str]
    data_tools: list[str]
    ai_llm: list[str]
    dev_habits: list[str]
    cs_foundations: list[str]

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "languages": self.languages,
            "backend": self.backend,
            "frontend": self.frontend,
            "data_tools": self.data_tools,
            "ai_llm": self.ai_llm,
            "dev_habits": self.dev_habits,
            "cs_foundations": self.cs_foundations,
        }


@dataclass(slots=True)
class ToneRules:
    overall: list[str]
    voice: list[str]
    dislikes: list[str]
    likes: list[str]


@dataclass(slots=True)
class ImpactGuide:
    label: str
    description: str | None
    samples: list[str]


@dataclass(slots=True)
class PlanGuide:
    label: str
    bullets: list[str]


@dataclass(slots=True)
class StructureGuide:
    greeting: str
    opener_guidance: str | None
    impact: ImpactGuide
    plan: PlanGuide
    stack_guidance: str | None
    close_guidance: str | None
    signoff: str


@dataclass(slots=True)
class LearningConfig:
    max_recent_notes: int = 3


@dataclass(slots=True)
class ExperienceHighlight:
    key: str
    title: str
    summary: str
    bullets: list[str]
    themes: list[str]


@dataclass(slots=True)
class CoverLetterKit:
    profile: ProfileSnapshot
    education: list[str]
    skills: SkillsCard
    experience: list[ExperienceHighlight]
    leadership_highlights: list[str]
    projects: list[ProjectCard]
    tone: ToneRules
    structure: StructureGuide
    tailoring_checklist: list[str]
    dos: list[str]
    donts: list[str]
    style_examples: list[str]
    reasoning_guidance: list[str]
    learning: LearningConfig

    def find_project_by_theme(self, theme: str) -> ProjectCard | None:
        normalized = theme.lower()
        for project in self.projects:
            if normalized in (t.lower() for t in project.themes):
                return project
        return None

    def list_project_keys(self) -> list[str]:
        return [project.key for project in self.projects]


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def _hydrate_project(card: dict) -> ProjectCard:
    return ProjectCard(
        key=str(card["key"]).lower(),
        name=card["name"],
        short_name=card.get("short_name", card["name"]),
        summary=card["summary"],
        talking_points=list(card.get("talking_points", [])),
        themes=list(card.get("themes", [])),
    )


def _hydrate_profile(data: dict) -> ProfileSnapshot:
    return ProfileSnapshot(
        bio=data["bio"],
        background=list(data.get("background", [])),
        technical_strengths={k: list(v) for k, v in data.get("technical_strengths", {}).items()},
        work_style=list(data.get("work_style", [])),
    )


def _hydrate_tone(data: dict) -> ToneRules:
    return ToneRules(
        overall=list(data.get("overall", [])),
        voice=list(data.get("voice", [])),
        dislikes=list(data.get("dislikes", [])),
        likes=list(data.get("likes", [])),
    )


def _hydrate_skills(data: Mapping[str, list[str]] | None) -> SkillsCard:
    data = data or {}
    return SkillsCard(
        languages=list(data.get("languages", [])),
        backend=list(data.get("backend", [])),
        frontend=list(data.get("frontend", [])),
        data_tools=list(data.get("data_tools", [])),
        ai_llm=list(data.get("ai_llm", [])),
        dev_habits=list(data.get("dev_habits", [])),
        cs_foundations=list(data.get("cs_foundations", [])),
    )


def _hydrate_impact(data: Mapping[str, object] | None) -> ImpactGuide:
    data = data or {}
    return ImpactGuide(
        label=data.get("label", "Impact highlights"),
        description=data.get("format") or data.get("guidance"),
        samples=list(data.get("samples", [])),
    )


def _hydrate_plan(data: Mapping[str, object] | None) -> PlanGuide:
    data = data or {}
    return PlanGuide(
        label=data.get("label", "First 60–90 days"),
        bullets=list(data.get("bullets", [])),
    )


def _hydrate_structure(data: dict) -> StructureGuide:
    return StructureGuide(
        greeting=data.get("greeting", "Dear Hiring Manager,"),
        opener_guidance=(data.get("opener") or {}).get("guidance"),
        impact=_hydrate_impact(data.get("impact_section")),
        plan=_hydrate_plan(data.get("plan_section")),
        stack_guidance=(data.get("stack_section") or {}).get("guidance"),
        close_guidance=(data.get("close") or {}).get("guidance"),
        signoff=data.get("signoff", "Sincerely,\nApoorva Chilukuri"),
    )


def _hydrate_learning(data: dict | None) -> LearningConfig:
    if not data:
        return LearningConfig()
    return LearningConfig(max_recent_notes=int(data.get("max_recent_notes", 3)))


def _hydrate_experience(entries: list[Mapping[str, object]] | None) -> list[ExperienceHighlight]:
    highlights: list[ExperienceHighlight] = []
    for entry in entries or []:
        highlights.append(
            ExperienceHighlight(
                key=str(entry.get("key", "")),
                title=entry.get("title", ""),
                summary=entry.get("summary", ""),
                bullets=list(entry.get("bullets", [])),
                themes=list(entry.get("themes", [])),
            )
        )
    return highlights


def _build_kit(data: dict) -> CoverLetterKit:
    projects = [_hydrate_project(card) for card in data.get("projects", [])]
    if not projects:
        raise ValueError("Cover letter kit must include at least one project.")

    return CoverLetterKit(
        profile=_hydrate_profile(data["profile"]),
        education=list(data.get("education", [])),
        skills=_hydrate_skills(data.get("skills_card")),
        experience=_hydrate_experience(data.get("experience_highlights")),
        leadership_highlights=list(data.get("leadership_highlights", [])),
        projects=projects,
        tone=_hydrate_tone(data["tone"]),
        structure=_hydrate_structure(data["structure"]),
        tailoring_checklist=list(data.get("tailoring_checklist", [])),
        dos=list(data.get("dos", [])),
        donts=list(data.get("donts", [])),
        style_examples=list(data.get("style_examples", [])),
        reasoning_guidance=list(data.get("reasoning_guidance", [])),
        learning=_hydrate_learning(data.get("learning")),
    )


@lru_cache(maxsize=1)
def load_cover_letter_kit(path: str | Path | None = None) -> CoverLetterKit:
    resolved = Path(path) if path else KIT_PATH
    if not resolved.exists():
        raise FileNotFoundError(f"Cover letter kit not found at {resolved}")
    data = _load_yaml(resolved)
    return _build_kit(data)


def cover_letter_kit_hash(path: str | Path | None = None) -> str:
    resolved = Path(path) if path else KIT_PATH
    if not resolved.exists():
        raise FileNotFoundError(f"Cover letter kit not found at {resolved}")
    digest = hashlib.sha1(resolved.read_bytes()).hexdigest()
    return digest


def summarise_rules(rules: Iterable[str]) -> str:
    return "; ".join(rules)
