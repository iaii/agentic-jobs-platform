"""Tier 2 eval: pairwise (A/B) comparison of two cover letter variants.

Per design doc §6, Tier 2 — instead of comparing absolute HM scores (noisy
on an 8B judge), this asks the judge to pick a winner between two letters
for the same job, Bradley-Terry/Arena style. Run this across 10-15 JDs when
testing a specific pipeline change (e.g. "does X help?") and aggregate the
win/loss/tie table.

Usage:
    python -m evals.run_pairwise --jd path/to/jd.txt --a path/to/letter_a.md --b path/to/letter_b.md
    python -m evals.run_pairwise --jd path/to/jd.txt --a-dir variantA/ --b-dir variantB/

Each `--a-dir`/`--b-dir` should contain matching filenames (one cover letter
per JD); `--jd-dir` should contain matching JD text files. Pairs are matched
by filename stem.

This is scaffolding: the LLM pairwise call is wired up via the existing
`call_llm` runner (no new LLM client), but prompt engineering for judge
reliability is left as future work — see TODO below.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from agentic_jobs.services.llm.runner import call_llm

PAIRWISE_SYSTEM_PROMPT = """You are an experienced hiring manager comparing two cover letters \
submitted for the same job posting. Read the job description and both letters, then decide \
which letter is the stronger application overall — considering fit to the role's \
requirements, specificity of evidence, and writing quality.

Respond with strict JSON only, in this exact shape:
{"winner": "A" | "B" | "tie", "reason": "<one or two sentence justification>"}
"""


def _build_user_message(jd_text: str, letter_a: str, letter_b: str) -> str:
    return (
        f"## Job Description\n{jd_text.strip()}\n\n"
        f"## Letter A\n{letter_a.strip()}\n\n"
        f"## Letter B\n{letter_b.strip()}\n\n"
        "Which letter is the stronger application — A, B, or tie?"
    )


async def compare_pair(jd_text: str, letter_a: str, letter_b: str) -> dict:
    """Run one pairwise comparison via the existing LLM runner.

    TODO: this is a minimal first pass — for production use, consider
    randomizing A/B order per call to control for position bias, and
    running each pair multiple times (see run_self_consistency.py) to
    gauge judge variance on pairwise calls too.
    """
    response = await call_llm(
        PAIRWISE_SYSTEM_PROMPT,
        _build_user_message(jd_text, letter_a, letter_b),
        temperature=0.3,
    )
    raw = response.content
    winner = raw.get("winner", "tie")
    if winner not in ("A", "B", "tie"):
        winner = "tie"
    return {"winner": winner, "reason": raw.get("reason", "")}


async def run_single(jd_path: Path, a_path: Path, b_path: Path) -> None:
    jd_text = jd_path.read_text(encoding="utf-8")
    letter_a = a_path.read_text(encoding="utf-8")
    letter_b = b_path.read_text(encoding="utf-8")

    result = await compare_pair(jd_text, letter_a, letter_b)
    print(json.dumps(result, indent=2))


async def run_batch(jd_dir: Path, a_dir: Path, b_dir: Path) -> None:
    """Match files by stem across the three directories and run pairwise comparisons."""
    a_files = {p.stem: p for p in a_dir.glob("*")}
    b_files = {p.stem: p for p in b_dir.glob("*")}
    jd_files = {p.stem: p for p in jd_dir.glob("*")}

    common = sorted(set(a_files) & set(b_files) & set(jd_files))
    if not common:
        print("No matching (jd, A, B) file triples found by filename stem.")
        return

    wins_a = wins_b = ties = 0
    rows: list[dict] = []

    for stem in common:
        result = await compare_pair(
            jd_files[stem].read_text(encoding="utf-8"),
            a_files[stem].read_text(encoding="utf-8"),
            b_files[stem].read_text(encoding="utf-8"),
        )
        winner = result["winner"]
        if winner == "A":
            wins_a += 1
        elif winner == "B":
            wins_b += 1
        else:
            ties += 1
        rows.append({"sample": stem, **result})
        print(f"  {stem}: winner={winner}  ({result['reason']})")

    total = len(common)
    print()
    print("=== Win/Loss/Tie table ===")
    print(f"{'Variant':<10}{'Wins':<8}{'Win rate':<10}")
    print(f"{'A':<10}{wins_a:<8}{wins_a / total:.1%}")
    print(f"{'B':<10}{wins_b:<8}{wins_b / total:.1%}")
    print(f"{'Tie':<10}{ties:<8}{ties / total:.1%}")
    print(f"\nTotal pairs: {total}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Tier 2: pairwise A/B comparison of cover letter variants")
    parser.add_argument("--jd", type=Path, help="Path to a single JD text file")
    parser.add_argument("--a", type=Path, help="Path to variant A's cover letter")
    parser.add_argument("--b", type=Path, help="Path to variant B's cover letter")
    parser.add_argument("--jd-dir", type=Path, help="Directory of JD text files (batch mode)")
    parser.add_argument("--a-dir", type=Path, help="Directory of variant A cover letters (batch mode)")
    parser.add_argument("--b-dir", type=Path, help="Directory of variant B cover letters (batch mode)")
    args = parser.parse_args()

    if args.jd and args.a and args.b:
        asyncio.run(run_single(args.jd, args.a, args.b))
    elif args.jd_dir and args.a_dir and args.b_dir:
        asyncio.run(run_batch(args.jd_dir, args.a_dir, args.b_dir))
    else:
        parser.error("Provide either (--jd, --a, --b) for a single comparison, or (--jd-dir, --a-dir, --b-dir) for a batch run.")


if __name__ == "__main__":
    main()
