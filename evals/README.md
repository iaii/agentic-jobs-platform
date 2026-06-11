# Evals

Scaffolding for the eval harness described in the design doc's §6
("Evaluation paradigms"), `docs/design/langgraph-orchestration-and-eval-design.md`.

This is a skeleton — it covers Tier 1 and Tier 2 of the proposed 3-tier
harness. Tier 3 (calibration logging) and the golden datasets are future
work, not yet populated.

## Tier 1 — `run_self_consistency.py`

Runs `HiringManagerAgent.run()` N times (default 3) against a fixed sample
(a `CoverLetterDraft` + `ResearchBrief`, either loaded from a real
application by `--application-id` or a hardcoded synthetic example) and
reports the score variance/std-dev across runs.

This establishes the **noise floor** of the local LLM judge — if the
variance here is comparable to (or larger than) the score deltas you'd
expect from a pipeline change, absolute-score comparisons aren't
meaningful and you should rely on Tier 2 (pairwise) instead.

```
python -m evals.run_self_consistency --n 5
python -m evals.run_self_consistency --n 5 --application-id <uuid>
```

## Tier 2 — `run_pairwise.py`

Pairwise (A/B) comparison of two cover letter variants for the same job —
Bradley-Terry/Arena style, used to compare pipeline variants (e.g. "with
skill mapper" vs. "without"). Outputs a win/loss/tie table.

```
# Single pair
python -m evals.run_pairwise --jd job.txt --a variantA/letter.md --b variantB/letter.md

# Batch (matches files by stem across three directories)
python -m evals.run_pairwise --jd-dir jds/ --a-dir variantA/ --b-dir variantB/
```

The pairwise judge call is wired up via the existing `agentic_jobs.services.llm.runner.call_llm`
(no new LLM client). Position-bias control (randomizing A/B order) and
per-pair repeat runs are noted as TODOs in the script.

## Tier 3 — calibration log (future work)

Per the design doc, `evals/calibration_log.jsonl` would be an append-only
log of `{run_id, hm_fit_score, hm_quality_score, your_rating, timestamp}`,
populated periodically to track whether the LLM judge's scores correlate
with your own "would I send this" rating over time. **Not yet implemented.**

## Golden datasets (future work)

The design doc also calls for:
- `evals/golden_vault_dataset.yaml` — retrieval precision/recall/MRR golden set
- `evals/golden_skill_map.yaml` — `{requirement, candidate_bullets, expected_framing}` (depends on the Skill Mapper, which is out of scope for this pass)
- `evals/calibration_log.jsonl` — Tier 3 log described above

None of these exist yet. They're noted here so the eventual Tier 1
component-regression scripts (retrieval precision/recall, fabrication
flag-rate) have an obvious home once they're written.
