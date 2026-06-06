from agentic_jobs.services.llm.style_kit import load_cover_letter_kit


def test_cover_letter_kit_loads() -> None:
    kit = load_cover_letter_kit()
    assert kit.profile.bio
    assert len(kit.projects) >= 1
    # The kit must carry the no-em-dash rule among its don'ts (wording may vary).
    assert any("em dash" in dont.lower() for dont in kit.donts)
