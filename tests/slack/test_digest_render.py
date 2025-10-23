import uuid

from agentic_jobs.services.slack.digest import DigestRow, build_digest_blocks


def test_build_digest_blocks_includes_actions() -> None:
    row = DigestRow(
        job_id=uuid.uuid4(),
        title="Backend Software Engineer",
        company="Acme Corp",
        location="Seattle, WA",
        url="https://example.com/job",
        score=0.82,
        rationale="title fit, geo boost",
    )

    blocks = build_digest_blocks([row])
    assert len(blocks) == 2
    section = blocks[0]
    assert "Backend Software Engineer" in section["text"]["text"]
    assert "title fit, geo boost" in section["text"]["text"]
    actions = blocks[1]
    action_ids = [element["action_id"] for element in actions["elements"]]
    assert "open_jd" in action_ids
    assert "save_to_tracker" in action_ids


def test_build_digest_blocks_empty() -> None:
    blocks = build_digest_blocks([])
    assert len(blocks) == 1
    assert "No new roles" in blocks[0]["text"]["text"]
