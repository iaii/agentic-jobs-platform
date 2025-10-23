from __future__ import annotations

from typing import Dict, Iterable


def compose_rationale(
    features: Dict[str, float],
    job,  # models.Job, avoid import cycle typing
    cfg: dict,
) -> str:
    if not features:
        return ""

    # Build tags from features. Keep at most cfg[rationale][max_tags] by weight desc.
    items = sorted(features.items(), key=lambda kv: kv[1], reverse=True)

    tags: list[str] = []
    geo_tag: str | None = None
    geo_tie: bool = False

    for key, _w in items:
        if key.startswith("new_grad"):
            if "new grad" not in tags:
                tags.append("new grad")
        elif key.startswith("title:"):
            title_tag = key.split(":", 1)[1]
            if title_tag == "backend":
                tag = "backend"
            elif title_tag == "full stack":
                tag = "full stack"
            else:
                tag = title_tag
            if tag not in tags:
                tags.append(tag)
        elif key.startswith("skill:"):
            skill = key.split(":", 1)[1]
            if skill not in tags:
                tags.append(skill)
        elif key.startswith("ai_group"):
            if "AI/Agents" not in tags:
                tags.append("AI/Agents")
        elif key.startswith("geo_tie:"):
            geo_tie = True
        elif key.startswith("geo:"):
            region = key.split(":", 1)[1]
            # Keep best (highest weight appears first already)
            if not geo_tag:
                geo_tag = region

    if geo_tag:
        if geo_tie:
            tags.append(f"{geo_tag} (remote)")
        else:
            tags.append(geo_tag)

    max_tags = int(cfg.get("rationale", {}).get("max_tags", 4))
    tags = tags[:max_tags]
    return " + ".join(tags)


