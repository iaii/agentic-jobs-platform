from agentic_jobs.services.llm.style_kit import load_cover_letter_kit


def test_cover_letter_kit_loads() -> None:
    kit = load_cover_letter_kit()
    assert kit.profile.bio
    assert len(kit.projects) >= 1
    assert "No em dashes" in kit.donts
