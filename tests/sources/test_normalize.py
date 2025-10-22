from agentic_jobs.services.sources.normalize import (
    compute_hash,
    extract_requirements,
    html_to_text,
)


def test_html_to_text_strips_markup(load_fixture) -> None:
    html = load_fixture("gh_job_detail_engineer.html")
    text = html_to_text(html)

    assert "Software Engineer" in text
    assert "<" not in text
    assert "Build reliable backend services." in text


def test_extract_requirements_returns_bullets(load_fixture) -> None:
    html = load_fixture("gh_job_detail_engineer.html")
    requirements = extract_requirements(html)

    assert any("Python" in item["value"] for item in requirements)
    assert all(item["type"] in {"bullet", "text"} for item in requirements)


def test_compute_hash_is_stable() -> None:
    text = "Build APIs and ensure reliability."
    hash_one = compute_hash("Software Engineer", "TestOrg", text)
    hash_two = compute_hash("Software Engineer", "TestOrg", text)
    hash_three = compute_hash("Software Engineer", "TestOrg", text + " Extra")

    assert hash_one == hash_two
    assert hash_one != hash_three
