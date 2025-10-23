from datetime import datetime

from agentic_jobs.db import models
from agentic_jobs.services.ranking import score_job, get_ranking_config


def _job(title: str, jd: str, loc: str) -> models.Job:
    j = models.Job(
        title=title,
        company_name="X",
        location=loc,
        url="https://example",
        source_type=models.JobSourceType.GREENHOUSE,  # type: ignore[attr-defined]
        domain_root="boards.greenhouse.io",
        submission_mode=models.SubmissionMode.ATS,  # type: ignore[attr-defined]
        jd_text=jd,
        requirements=[],
        job_id_canonical="GH:1",
        scraped_at=datetime.utcnow(),
        hash="h",
    )
    return j


def test_title_signal_weight() -> None:
    r = score_job(_job("Software Engineer, Backend", "", "NYC"))
    cfg = get_ranking_config()
    assert r.features
    assert any(k.startswith("title:") for k in r.features)
    assert round(sum(r.features.values()), 5) >= cfg["weights"]["title_match"]


def test_new_grad_phrase_weight() -> None:
    r = score_job(_job("SWE", "This is an entry level role", "Seattle"))
    assert r.features.get("new_grad", 0) > 0


def test_skills_and_ai_group_cap() -> None:
    jd = (
        "We use Python, Java, C++, Swift, SQL, MySQL, PostgreSQL, MongoDB, HTML, CSS, Linux, Power BI. "
        "We also leverage LangChain, NumPy, SQLAlchemy, Streamlit, CrewAI, Ollama. "
        "Our stack includes Agentic AI, AI Agent, RAG, retrieval-augmented, LLM fine-tuning, multimodal."
    )
    r = score_job(_job("Engineer", jd, "San Francisco"))
    # AI group capped at 0.10
    assert r.features.get("ai_group", 0) == 0.10


def test_geo_core_and_remote_tie() -> None:
    r = score_job(_job("Engineer", "", "Remote US — Bay Area preferred, San Jose"))
    assert any(k.startswith("geo:") for k in r.features)
    assert any(k.startswith("geo_tie:") for k in r.features)


def test_clamp_to_one() -> None:
    # Build a case with many matches to exceed 1.0
    jd = (
        "new grad entry level university graduate Python Java C++ Swift SQL MySQL PostgreSQL Postgres MongoDB "
        "HTML CSS Linux Power BI LangChain NumPy SQLAlchemy Streamlit CrewAI Ollama Agentic AI AI Agent RAG retrieval-augmented LLM fine-tuning multimodal"
    )
    r = score_job(_job("Software Engineer, Backend Full Stack SWE", jd, "NYC Remote"))
    assert 0.0 <= r.score <= 1.0
    assert r.score == 1.0


def test_rationale_tags() -> None:
    r = score_job(_job("Backend Software Engineer", "entry level Python", "San Francisco (Hybrid)"))
    assert r.rationale
    # At most 4 tags
    assert len([t for t in r.rationale.split(" + ") if t]) <= 4


