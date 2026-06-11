# Design Doc v2: Skill Framing, Holistic Agent Architecture, Hiring-Manager Redesign, LangGraph Orchestration

## Context

This extends the earlier vault-retrieval/research-fallback design doc with a deeper question: even with perfect retrieval, how does the **writer** know *which* embeddings/experience bullets to lean on, and how does it *reframe* AI-flavored work to speak to a JD asking for "core SWE" skills? Separately, the current Hiring Manager rubric judges letter *quality* but not candidate *fit*, and doesn't give the writer actionable, requirement-by-requirement feedback. Finally: should LangGraph orchestrate any of this?

This is a **design discussion document** — capturing architecture options and tradeoffs for review, not an implementation plan yet.

---

## Summary of everything in this doc

| Area | Key tech | What it solves | Status |
|---|---|---|---|
| **Retrieval — multi-query** | LLM generates 1-3 paraphrased queries from JD instead of one static heuristic slice | Static keyword-marker query misses requirements phrased outside the marker section | New (prior doc, Phase 1) |
| **Retrieval — HyDE** | LLM generates a hypothetical *vault-note-style* passage from the requirement, embeds *that* for search | Vocabulary/register mismatch — JD-speak ("distributed systems") vs. notes-speak ("Redis cache across worker nodes") never scores high on cosine similarity even when conceptually related | New (this doc, §1a) |
| **Retrieval — hybrid BM25 + graph re-rank** | `rank-bm25` lexical score + cosine similarity, combined; wikilink graph multi-hop with score decay | Catches exact-term matches semantic search dilutes; surfaces related-but-not-directly-similar notes via graph | New (prior doc, Phase 2) |
| **Skill Mapper** | Two-stage: embedding shortlist (cheap, deterministic) → LLM picks from shortlist + writes `underlying_skill`/`framing_angle` (enumerated-choice pattern, same as researcher's `valid_experience_keys`) | "AI lens vs. core SWE lens" — your notes describe work in domain-specific terms; JD wants generalizable-skill framing. Without this, writer either keeps mismatched framing or freelances (fabrication risk) | New (this doc, §2) |
| **Writer — framing guardrail** | Explicit "vocabulary swap OK, new facts/numbers not OK" rule + worked example pair in system prompt | Bounds the Skill Mapper's reframing so it doesn't become invention | New (this doc, §2) |
| **Writer — bounded tool-calling** | Optional `search_vault(query)` tool, 0-2 calls max, config-gated | Escape hatch when upstream retrieval/skill-map missed something — first agentic (non-single-pass) step in the pipeline | New (this doc, §1), candidate for LangGraph spike (§5) |
| **Hiring Manager — fit dimension** | Per-`jd_requirement` structured scoring (`addressed/strength/suggestion`), reusing the same requirements list as researcher/skill-mapper | Current rubric judges letter *quality*, not candidate *fit* — you can't tell from a 7/10 whether the letter is well-written but a poor match | New (this doc, §4) |
| **Hiring Manager — quality dimension (refined)** | 5-dim rubric with explicit 0/1/2 anchors, new "Hook/Opener" dimension, fit-pass-before-quality-pass ordering | Old rubric's anchors were vague/subjective, no separate opener scoring, no anchoring sequence for the 8B judge | Refined (this doc, §4) |
| **Two-threshold verdict** | `fit_pass_threshold` + `quality_pass_threshold`, both must pass | Single threshold conflates "well written" and "good fit" — a letter could pass on prose alone while addressing nothing the JD asked for | New (this doc, §4) |
| **Genuine gaps surfacing** | `genuine_gaps` list (skill_map `coverage:none` ∩ HM `strength:0`) shown in Slack/agent_log | Prevents the system from quietly papering over a poor-fit job; gives you visibility before sending | New (this doc, §4) |
| **Fabrication check** | Deterministic regex/lexical diff: extract numbers + known proper-nouns from writer's *inputs*, compare against draft, flag novel terms (not hard-block) | "Vocabulary OK, new facts not OK" guardrail is unenforceable by prompting alone on an 8B model — this is a cheap, no-LLM-call backstop | New (this doc, §7.2) |
| **Eval harness — 3 tiers** | (1) component regression: retrieval P/R/MRR, fabrication flag-rate, HM self-consistency variance; (2) pairwise A/B variant comparison (Bradley-Terry style, not absolute scores); (3) calibration log (your rating vs. HM score over time) | No eval infrastructure exists today — without this, can't tell if any of the above changes actually help vs. just adding 8B noise | New (this doc, §6) |
| **RAGAS-style metrics** | Faithfulness ≈ fabrication check (graded); Context Precision/Recall ≈ golden retrieval eval; Answer Relevancy ≈ fit dimension | Gives a standard vocabulary/structure for the metrics above, plus a graded (not binary) faithfulness score for trend-tracking | Mapped (this doc, §6) |
| **LangGraph orchestration** | Incremental: TypedDict `PipelineState`, refactor agents to state-in/state-out functions, spike the writer tool-calling loop as an isolated LangGraph subgraph first | Current single linear loop doesn't need it, but §3's new HM→Skill-Mapper/Researcher branch + §1's tool-calling do benefit from conditional-edge graphs and checkpointed replay | Prep steps defined (this doc, §5) |

---

## 1. How does the Writer know which embeddings/experience to use?

**Current reality:** the writer doesn't choose anything. All retrieval happens *before* the writer runs:
- Coordinator extracts a vault query from the JD → `VaultRetriever.search()` → top-k vault matches
- ResearcherAgent picks `primary_experience_key` + up to 2 `matched_experience_keys` from your verified kit
- Writer receives these as fixed inputs (`vault_context`, `verified_experience_bullets`) and is told "only use facts from these"

So the writer is a pure **consumer** of a single upstream retrieval pass — it cannot ask "wait, I need something else" mid-generation.

**Limitation:** if the retrieved bullet doesn't actually map well to the JD requirement it was matched against (see §2), the writer has no recourse — it just writes around the gap or, worse, papers over it with vague language (which the current reviewer rubric penalizes as "Specificity: 0" but doesn't explain *why*).

**Design option — agentic retrieval (optional, bounded):**
Expose `search_vault(query: str)` as a tool the writer LLM can call 0-2 times during generation, capped by config (`writer_max_tool_calls`). This is the kind of thing LangGraph's tool-calling loop is built for (see §5). Use sparingly — most of the "what to look for" decision should be resolved *before* the writer via the skill-mapping step below, so the writer rarely needs to ask.

### §1a. Retrieval refinement: the title/vocabulary mismatch problem (HyDE)

A separate but related question: **how does retrieval know what to search for, given a vault note's title might not literally name the JD requirement it actually addresses?**

`VaultRetriever.search()` already does *semantic*, not keyword, search — it embeds the full `section_text` (heading + body), so a section titled "RAG Pipeline Architecture" *can* match a query like "distributed systems experience" if the body discusses caching/scaling/latency in terms that are semantically close. This isn't a literal title-keyword match.

**But embedding similarity degrades as vocabulary register diverges**, even when concepts genuinely overlap. "Distributed systems experience" (JD-speak) vs. "built a retrieval pipeline with a Redis cache layer across worker nodes" (notes-speak) are conceptually related but lexically distant — cosine similarity will likely be moderate, and a less-relevant note using more JD-like vocabulary could outrank it.

**Proposed addition — HyDE (Hypothetical Document Embeddings):** instead of embedding the JD requirement as written, ask the LLM to generate a **hypothetical passage written in your-notes style** that would satisfy the requirement — then embed *that* for retrieval. This closes the register gap because the query embedding now lives in "notes vocabulary" space, the same space your actual notes were embedded in.

```python
async def hyde_query(requirement: str) -> str:
    """Generate a hypothetical vault-note passage for a JD requirement, for embedding."""
    prompt = (
        f"Write a 2-3 sentence note (in first person, technical, the way a personal "
        f"engineering journal entry would be written) describing a project that would "
        f"satisfy this job requirement: {requirement}"
    )
    return await call_llm(prompt, temperature=0.3)
    # embed this output instead of (or in addition to) the raw requirement text
```

**How this combines with multi-query (Phase 1, prior doc):** generate one HyDE passage *per* multi-query variant, embed both the raw query and the HyDE passage, search with both, merge results by `(file_path, heading)` keeping max score — same dedup pattern already proposed for multi-query. This roughly doubles embedding calls (cheap — local embedder) but adds zero extra *generation* LLM calls beyond the existing multi-query step if folded into the same call ("for each query, also write a hypothetical note passage that would satisfy it").

**Caveat:** HyDE passages are themselves LLM-generated text — if the hypothetical passage describes something *plausible but not actually in your notes*, retrieval could be steered toward tangentially-related notes that happen to share vocabulary with the hallucinated hypothetical. This is a retrieval-quality risk, not a fact-fidelity risk (nothing from the HyDE passage reaches the writer — it's discarded after retrieval), but it's exactly the kind of thing the golden-dataset eval (§6, Tier 1) should catch: if HyDE variant's precision@k is *worse* than plain multi-query, drop it.

### Summary: Key tech/design for this section
- **Current state:** single-shot retrieval, writer is a passive consumer — simple, deterministic, cheap.
- **Proposed addition:** optional bounded tool-call (`search_vault`) for the writer, gated by config, used as an escape hatch rather than the primary mechanism.
- **Proposed addition (§1a):** HyDE — embed an LLM-generated "hypothetical note" instead of/alongside the raw requirement, to close the JD-vocabulary vs. notes-vocabulary gap that pure semantic search doesn't fully solve.
- **Engineering note:** this is the first place an "agentic" (tool-calling) pattern would enter the pipeline — everything else today is single-pass prompt→response.

---

## 2. The "AI lens vs. core SWE lens" reframing problem

This is the central new design problem and probably the highest-leverage one.

**The problem, concretely:** your vault notes and verified experience bullets describe work *as you experienced it* — e.g., "built a RAG pipeline with hybrid retrieval over a 50k-doc corpus, p95 latency 180ms." A JD for a "Core Backend Engineer" role cares about: distributed systems design, API contracts, performance optimization, data pipeline reliability — all of which *are* present in that bullet, but the bullet's surface framing is "AI/RAG," not "backend systems."

Today, the writer is told: *"Only facts from verified_experience_bullets or vault_context. No invented metrics."* This is a **fact-fidelity guardrail** but says nothing about **framing/emphasis** — so the writer either (a) keeps the AI framing verbatim (mismatch with JD), or (b) the model freelances a different framing, which risks drifting into invented specifics ("distributed systems" → model adds "across 12 microservices" that was never in the source).

**Proposed new step: a "Skill Mapper"** (could be a new lightweight LLM call, or folded into the existing ResearcherAgent's output — leaning toward a separate step since it has a distinct, narrow job and its own evaluable output).

### What is a "matched experience key", concretely?

This was asking to be clarified. It is **not** an embedding — it's a plain string ID into a small, hand-maintained YAML file: `agentic_jobs/profile/cover_letter_kit.yaml`, loaded into `CoverLetterKit.experience: list[ExperienceHighlight]` (`agentic_jobs/services/llm/style_kit.py:95-99`):

```python
@dataclass(slots=True)
class ExperienceHighlight:
    key: str            # e.g. "rag_pipeline_v2" — stable ID, what the researcher selects
    title: str          # e.g. "Retrieval-Augmented Generation Pipeline"
    summary: str        # 1-2 sentence overview
    bullets: list[str]  # the actual verified accomplishment statements
    themes: list[str]   # tags you've assigned, e.g. ["AI/ML", "backend", "performance"]
```

So: **the kit is a small curated dataset you maintain by hand** (a handful of experience entries, each with a few bullets), completely separate from the vault embeddings. The researcher's `matched_experience_keys` are just `key` values from this list — the coordinator looks them up and hands the *full* `ExperienceHighlight` object (title/summary/bullets/themes) to downstream agents. No vector search is involved in selecting these; it's an LLM picking from a short enumerated list it's shown directly in its prompt (`valid_experience_keys`).

The **vault** (`vault_context`, from embeddings) is a *second, larger, separate* source — your raw Obsidian notes, retrieved semantically. So the writer actually has two pools of material: (1) a handful of curated, pre-written `ExperienceHighlight` bullets (small, vetted, reused across letters), and (2) ad-hoc vault excerpts (larger corpus, retrieved per-job, more raw/unedited).

The Skill Mapper (below) operates over **both pools** — for each JD requirement, it can point to an `ExperienceHighlight.bullets[i]` entry, a vault excerpt, or both.

**Inputs:** `jd_requirements` (already extracted by researcher), the resolved `ExperienceHighlight` objects for `matched_experience_keys` (title/summary/bullets/**themes**), top vault excerpts.

**Output — a structured skill map**, one entry per JD requirement:
```
{
  "requirement": "experience with distributed systems / performance optimization",
  "source_bullet_ref": "rag_pipeline_v2",  // must be a real key from the kit
  "underlying_skill": "Designed and optimized a high-throughput retrieval system; reduced p95 latency from Xms to 180ms",
  "framing_angle": "Lead with the latency/throughput optimization work, not the 'RAG'/AI terminology — describe it as a retrieval/data system",
  "coverage": "strong" | "partial" | "none"
}
```

**Guardrail for the writer:** explicitly allowed to **change vocabulary/framing** for a fact (e.g., call a RAG pipeline "a high-throughput retrieval system") **but not allowed to add any technology, number, or outcome not present in the source bullet/vault text**. This needs to be a hard rule in the writer system prompt, ideally with an example pair (AI-framed source → SWE-framed output) showing what's allowed (vocabulary swap) vs. not allowed (new claims).

**Why this is "allowed":** it's the same content, different lens — like describing the same project as "shipped a feature" to a PM and "implemented the state machine" to an engineer. The constraint is facts, not vocabulary.

**Coverage gaps:** when `coverage: "none"` — no experience addresses a requirement at all — that's valuable signal (see §4, fit dimension). The system shouldn't hide this; it should decide what to do with it (acknowledge implicitly by not over-claiming, or surface to you as "this job may not be a great fit on requirement X").

### Does the Skill Mapper use LLM reasoning, and how does it pick "best" skill?

Yes — and it has to, because "what's the underlying generalizable skill behind this AI-framed bullet" is a semantic abstraction task, not a lookup. But the design should **bound the LLM's job to reduce hallucination risk**, using a two-stage hybrid:

**Stage 1 — cheap candidate shortlist (embeddings, deterministic, reused infrastructure):**
For each `jd_requirement`, compute cosine similarity (same `nomic-embed-text-v1.5` embedder already used for the vault) between the requirement text and:
- each `ExperienceHighlight.bullets[i]` (embed these once, cache — they're static/curated)
- each top vault excerpt (already embedded)

Take the top 2-3 candidates per requirement. This step is pure math, no LLM, and **constrains** what the LLM in Stage 2 is even allowed to talk about.

**Stage 2 — LLM reframing over the shortlist (the actual "reasoning" step):**
The LLM receives, per requirement: the requirement text + the 2-3 shortlisted bullets/excerpts (verbatim) + their `themes` tags. It's asked to:
1. Pick which shortlisted item (if any) is the best match — **must be one of the items shown, by reference/index, not free text** (same "select from enumerated list" pattern the researcher already uses for `valid_experience_keys` — proven to prevent invented references).
2. Write `underlying_skill` — a 1-sentence restatement of *what the chosen bullet demonstrates*, in vocabulary independent of the AI/domain framing (this is the genuinely generative part).
3. Write `framing_angle` — a short instruction to the writer (e.g., "lead with the latency number, describe as a retrieval/data system not a RAG pipeline").
4. If nothing in the shortlist is a real match, mark `coverage: "none"` — this is a valid, expected output, not a failure.

**Why hybrid over pure-LLM:** a pure-LLM "search my whole kit + vault and pick the best bullet for this requirement" call is more prone to (a) picking something tangential because it sounds related, (b) inventing a synthesis that mixes two bullets together (the fabrication risk from §7.2). Pre-filtering with embeddings means the LLM is doing *reframing of given material*, not *search* — a narrower, more checkable task.

### Summary: does this answer "is reframing an allowed action"?
Yes, with the structure above: embeddings do the *retrieval/selection narrowing* (deterministic), the LLM does the *reframing* (generative but constrained to the shortlisted text), and the writer is told explicitly which framing angle to use per requirement. The "AI lens → SWE lens" translation happens in `underlying_skill`/`framing_angle`, generated *from* the verbatim source text the LLM was shown — not from the model's general knowledge of what "distributed systems experience" sounds like.

### Summary: Key tech/design for this section
- **New artifact:** a structured `skill_map` (JSON list, one entry per JD requirement) — requirement → source bullet → underlying generalizable skill → framing instruction → coverage level.
- **New LLM call:** one additional 8B call per pipeline run (or folded into researcher's existing call — cheaper but conflates two concerns).
- **New guardrail:** writer prompt gets a "framing vs. fabrication" rule with a worked example — vocabulary changes allowed, new facts/numbers not.
- **New signal:** `coverage: none` entries become the seed for the Hiring Manager's "fit" dimension (§4) and for surfacing genuine gaps to you.

---

## 3. Holistic pipeline view

```
Job posting
   │
   ▼
Researcher  ──► research_brief: { jd_requirements, primary/matched experience keys, company_intelligence }
   │
   ▼
Skill Mapper ──► skill_map: [{requirement, source_bullet, underlying_skill, framing_angle, coverage}]
   │
   ▼
Writer  ◄──(optional bounded tool call: search_vault)
   │  uses: kit bullets + vault_context + skill_map (framing) + reviewer_feedback (on revisions)
   ▼
Hiring Manager ──► { fit_feedback: [{requirement, addressed, strength, suggestion}], quality_score, verdict }
   │
   ├─ pass ──► Persist + Slack
   │
   └─ revise ──► back to Writer with structured fit_feedback + quality feedback
                 (and, if a fit gap is fundamental — e.g. coverage:none and HM flags it as
                  a blocking gap — optionally loop back to Skill Mapper / Researcher to see
                  if a *different* matched_experience_key would cover it better)
```

The new branch (HM → back to Skill Mapper/Researcher, not just Writer) is the first place a genuinely **non-linear** graph topology appears — which is relevant to the LangGraph discussion below.

### Summary: Key tech/design for this section
- **Pipeline grows from 3 agents to 4** (Researcher, Skill Mapper, Writer, Hiring Manager), still mostly sequential.
- **One new conditional branch type:** HM feedback can target either the Writer (framing/quality fixes) or upstream (Skill Mapper/Researcher, if the issue is "wrong experience selected" rather than "poorly framed").
- **State that needs to flow end-to-end:** `skill_map` and `fit_feedback` are new objects threaded through the loop alongside the existing `research_brief`/`ReviewVerdict`.

---

## 4. Hiring Manager redesign — from "letter quality" to "fit + quality"

**Your concern, restated:** the current rubric (So-What, Specificity, Technical Depth, Redundancy, Voice) judges whether the letter *reads well* — it doesn't judge whether **you'd actually be a good fit for this role**, and its feedback ("strengths", "feedback", "overall_impression" as free text) isn't structured enough for the writer to act on precisely.

**Proposed: split into two dimensions, both produced by the same HM call (one LLM call, structured output with two sections) to avoid doubling latency:**

### Dimension A — Fit (new)
For each `jd_requirements` entry (same list the researcher/skill-mapper already produced — reuse, don't re-derive):
```
{
  "requirement": "...",
  "addressed_in_letter": true/false,
  "strength": 0-2,        // 0=not addressed, 1=mentioned, 2=convincingly demonstrated
  "suggestion": "..."     // concrete, e.g. "Tie the latency-optimization detail in
                           //  paragraph 2 explicitly back to 'distributed systems' —
                           //  right now it reads as an AI project, not a systems one."
}
```
Aggregate: `fit_score = mean(strength) / 2`, plus a list of `genuine_gaps` = requirements where `coverage: none` in the skill_map AND `strength: 0` here — i.e., not just "didn't mention it" but "couldn't have, given available material."

### Dimension B — Quality (refined from current rubric)

The current 5-dimension rubric (So-What / Specificity / Technical Depth / Redundancy / Voice, 0-2 each) is structurally fine but under-specified — the 0/1/2 anchors are somewhat subjective and there's no company-alignment dimension separate from "Specificity." Proposed refinement:

| Dimension | 0 | 1 | 2 | Notes |
|---|---|---|---|---|
| **Hook/Opener** | Generic ("I'm excited to apply...") | Mentions company name but generic reason | Names a *specific* product/problem/team from `company_intelligence` and ties it to candidate's interest | New — currently folded into "Specificity", split out because openers are make-or-break and easy to score in isolation |
| **"So What?"** | States facts without impact | Some paragraphs explain why it matters, others don't | Every paragraph answers "why should I care" | Same as current |
| **Technical Depth** | Vague ("complex systems", "large datasets") | Names tools/tech but no outcome | Concrete outcome + tool + number, *correctly framed per skill_map* (new tie-in) | Now explicitly checks the skill_map's `framing_angle` was applied |
| **Redundancy/Filler** | Repeats ideas/phrases | 1-2 redundant phrases | Every sentence adds new info | Same as current |
| **Voice/Confidence** | Corporate/templated language; banned-phrase hits | Mostly authentic, some filler | Direct, confident, shows not tells | Banned-phrase check stays a **deterministic pre-check**, not LLM-judged — auto-zeroes this dimension |

- Each dimension returns `{dimension, score, suggestion}` — the `suggestion` is a specific edit instruction, not a restatement of the score (e.g., not "improve technical depth" but "the bullet about the retrieval pipeline doesn't mention the 180ms latency number from your kit — add it").
- **Ordering matters for an 8B model:** do the **Fit pass first** (concrete, requirement-by-requirement, low ambiguity), *then* the Quality pass (more holistic/subjective). Anchoring on concrete judgments first tends to produce more consistent subjective judgments after — this is the same reasoning behind the existing "pre-scoring questions" pattern, just extended.
- Keep the banned-phrase hard check (deterministic, not LLM-judged — this is correctly a non-LLM gate).

### Combined verdict logic
```
overall = pass if (fit_score >= fit_threshold) AND (quality_score >= quality_threshold)
        else revise
```
Two thresholds instead of one — a letter can be well-written but a poor fit (revise to re-emphasize different experience), or a good fit but poorly written (revise for prose). The `feedback_to_address` payload to the writer should say *which* dimension(s) failed and route the per-requirement/per-dimension suggestions accordingly.

### "Genuine gaps" — what to do with them
If `genuine_gaps` is non-empty after the skill-mapper has already tried all matched experience keys, that's a real signal: this job may not be a strong match on paper. Don't have the writer paper over it. Options (not mutually exclusive):
- Surface `genuine_gaps` in the Slack summary alongside the score — lets you eyeball "this letter scored 8/10 but couldn't address requirement X" before you send it.
- Feed it back as a signal to whatever upstream job-scoring/filtering exists (outside this doc's scope, but worth noting — if the same gap recurs across many applications to similar roles, that's a profile/positioning insight, not a per-letter one).

### Summary: Key tech/design for this section
- **Same model, same call count** (one HM call), but **structured two-dimension output**: fit (per-requirement) + quality (per-rubric-dimension), each with scores AND suggestions.
- **Two thresholds** (`fit_pass_threshold`, `quality_pass_threshold`) replace the single `pipeline_pass_threshold`.
- **New artifact `genuine_gaps`**: requirements that are structurally unaddressable with current material — surfaced to you, not hidden.
- **Feedback routing:** writer revisions get targeted, per-requirement/per-dimension instructions instead of one paragraph of "overall_impression."

---

## 5. LangGraph — pros, cons, and where it actually fits

**What LangGraph would bring:**
- Native cyclic graphs with conditional edges — useful now that §3 introduces a **second loop target** (HM → Writer *or* HM → Skill Mapper/Researcher), which a hand-rolled `for revision_round in range(...)` loop handles awkwardly once there's more than one possible "back-edge."
- Tool-calling loops — needed if you adopt the optional `search_vault` tool for the writer (§1).
- Built-in state checkpointing — could give you step-by-step replay/debugging of a pipeline run (useful given how opaque "why did the 8B model produce this score" can be).
- Visualization of the graph — directly useful for the "holistic view" you're asking for; you'd get an actual diagram of the system, not just this doc.

**What it costs:**
- New dependency (`langgraph` + `langchain-core`) in a codebase that currently has zero LangChain ecosystem deps — version churn and another moving part to keep compatible with LM Studio's OpenAI-compatible endpoint (function-calling format quirks with local 8B models are a known rough edge).
- The **current** pipeline (single linear loop, one back-edge) doesn't really need it — a plain `while` loop with an enum state is simpler and has been working.
- Your most carefully-engineered invariant — *"coordinator validates experience keys against the kit before the writer ever sees them, so hallucinated experience is structurally impossible"* — is enforced by plain Python today. In a LangGraph node-based design, that validation needs to live in a node (or an edge condition) and it's easy to accidentally let unvalidated state pass through if the graph gets restructured later. This isn't a blocker, just a "don't lose this property" note.
- Debugging a graph framework's state across nodes is generally less transparent than reading a Python stack trace / print statements, especially for a project that's currently a one-person debugging effort.

**Recommendation:** don't rewrite the whole coordinator. The trigger for LangGraph being *worth it* is specifically **§3's new branch** (HM can route feedback to two different upstream agents) plus **§1's optional tool-calling**. If/when both of those land, LangGraph's conditional-edge model genuinely simplifies that routing logic vs. nested loops with manual state flags. Until then, the existing custom coordinator with one or two new branch flags is simpler and keeps your validation guarantees in plain, auditable Python.

A middle path: keep `coordinator.py` as the top-level orchestrator (plain Python), but wrap the Writer's tool-calling loop (§1) specifically in a small LangGraph subgraph — i.e., adopt it for the one piece that's *actually* agentic, not for the deterministic parts. This bounds the blast radius of the new dependency.

### Prep work — concrete steps to take now, since you want to move toward LangGraph

These are useful refactors *independent* of whether the full migration ever happens — they're good engineering hygiene that happens to also be exactly what LangGraph nodes need.

1. **Define `PipelineState` as a single typed object** (e.g. `TypedDict` or pydantic model) capturing everything that flows through the pipeline: `jd_text, research_brief, skill_map, draft, fit_feedback, quality_feedback, revision_round, scraped_pages, vault_matches, ...`. Today this state is implicitly threaded through coordinator method args/locals — making it one explicit object is the prerequisite for LangGraph (every node reads/writes slices of one shared state) and also just makes `coordinator.py` easier to reason about regardless.

2. **Refactor each agent's `.run()` to a `(state) -> partial_state_update` shape.** Currently e.g. `WriterAgent.run(research_brief, profile, kit, ...)` takes several positional args and returns a draft. Change to `WriterAgent.run(state: PipelineState) -> dict` returning just the keys it updates (`{"draft": ..., "word_count": ...}`). This is a mechanical refactor, doesn't change behavior, and is **literally the LangGraph node function signature** — so when you do adopt LangGraph, these become nodes with zero logic changes.

3. **Wrap deterministic steps the same way** — `_gather_company_data`, `_search_vault`, the experience-key validation step — as the same `(state) -> partial_state_update` shape. This is the step that needs the most care: the experience-key validation node must run *before* the writer node and its output (`verified_experience_bullets`) must be the *only* path by which experience reaches the writer in the graph — i.e., don't give the writer node direct access to the full kit in `PipelineState`, only to the validated subset. Encoding this as "what's in the state object the writer node receives" rather than "what the writer agent's Python method happens to be passed" is how you preserve the invariant in graph form.

4. **Spike: implement just the writer tool-calling subgraph (§1) in LangGraph, in isolation**, before touching `coordinator.py`. Concretely: a small standalone script/module (`agentic_jobs/services/agents/graph/writer_subgraph.py`) that takes a `PipelineState` slice, runs a LangGraph `StateGraph` with one LLM node + one `search_vault` tool node + a conditional edge ("call tool again, or finish"), and returns a draft. Test it against 3-5 real jobs, compare output to the current non-agentic writer.
   - This directly de-risks the biggest unknown in §5's cons list: **LM Studio's OpenAI-compatible endpoint + an 8B model's function-calling reliability with LangChain's tool-calling abstractions** — better to find out it's flaky on a 50-line spike than mid-migration.
   - Add `langgraph` + `langchain-core` as dependencies for this spike only, in an isolated module — `coordinator.py` doesn't import it yet.

5. **Decide adoption based on the spike + eval harness (§6, Tier 2):** run the spike's tool-calling writer vs. the current writer through pairwise comparison on the same 10-15 jobs. If tool-calling measurably improves win-rate *and* the LM Studio function-calling integration was stable in the spike, expand LangGraph to cover §3's HM→Skill-Mapper/Researcher branch next (the second trigger condition from the recommendation above). If either condition fails, you've spent ~a day on a spike instead of a full rewrite, and steps 1-3 (state refactor) were useful regardless.

### Summary: Key tech/design for this section
- **Trigger conditions for adopting LangGraph:** (a) writer gains tool-calling, and/or (b) HM feedback needs to route to >1 upstream agent.
- **Recommendation:** incremental adoption — a LangGraph subgraph for the agentic writer loop only, keep the deterministic coordinator (data gathering, validation, persistence) as plain Python.
- **Watch-out:** preserve the "validate experience keys before writer sees them" invariant explicitly as a graph node/edge condition if you do migrate that part.

---

## 6. Evaluation paradigms — what's the industry standard, and what should *we* build

This system now has ~5 places where "is this good?" matters: retrieval, skill-mapping, writing, fit-judgment, quality-judgment. Generic "vibes" testing won't scale across that many components. Here's what's standard practice for LLM/RAG pipelines, mapped to what's feasible at your scale (one user, local 8B model, ~50-note vault).

### Industry-standard approaches (and their relevance here)

| Approach | What it is | Relevance to this project |
|---|---|---|
| **RAGAS-style component metrics** (faithfulness, context precision/recall, answer relevancy) | Decompose RAG quality into: did retrieval get the right context? did generation stay faithful to context? is the output relevant to the query? | Directly applicable — §7.2's fabrication check *is* a faithfulness metric. Retrieval precision/recall (already in the prior doc's Phase 0) is context precision/recall. |
| **LLM-as-judge with rubric** | What you're already doing (HM agent) | You have this — the open question is *judge reliability*, not whether to have one |
| **Judge calibration against humans** | Periodically have a human score the same outputs the LLM judge scored; measure agreement (correlation, Cohen's κ) | **High value here, low cost.** You're the "human" — you already read every cover letter before sending. Capturing your reaction (1-5 "would I send this as-is?") against the HM's score, even informally, tells you if the 8B judge is trustworthy *at all* before investing in the fit/quality redesign. |
| **Self-consistency / variance check** | Run the same judge call N times (temp > 0) on identical input, measure score variance | Cheap, automatable, directly answers "is the 8B model's score noise bigger than the signal we're trying to measure?" — run this *before* comparing pipeline variants, otherwise you can't tell improvement from noise |
| **Pairwise preference (A/B) over absolute scoring** | Instead of "score this 0-10", ask "which of these two letters is better, A or B?" — Bradley-Terry / Elo-style aggregation (this is how LMSYS Chatbot Arena works) | **Likely your best tool for comparing pipeline variants** (e.g. "with skill-mapper" vs "without"). LLM judges are demonstrably more reliable at relative comparisons than absolute scores — less anchoring/scale-drift. For evaluating "did the redesign help", generate both variants for the same job and have either the HM *or you* pick a winner. |
| **Golden/regression datasets per component** | Fixed input→expected-output pairs, run on every change | You already planned this for retrieval (Phase 0 of prior doc). Extend to: skill-map golden set (`{requirement, candidate_bullets} → expected framing_angle`, hand-labeled by you, ~10 examples), and an end-to-end golden set (`{job} → your actual 1-5 "would send" rating from past applications`, if you have history) |
| **Shadow/offline evaluation** | Run a new pipeline variant on past inputs *without* using its output, just logging — compare against what actually happened | You likely have a backlog of past `PipelineRun` records with real JDs and the letters you actually sent. Re-run the new pipeline on those JDs offline, compare new vs. old output on the same job — no live risk |
| **Outcome-based eval (the real ground truth)** | Did the application lead to a response/interview? | Long feedback loop (weeks), small N, confounded by many factors — not a near-term metric, but worth *tagging* `PipelineRun` records with outcomes if/when you hear back, purely for future retrospective analysis |

### Concrete proposal: a 3-tier eval harness

**Tier 1 — Component regression (fast, runs on every change):**
- Retrieval: precision@k/recall@k/MRR against golden vault dataset (from prior doc, Phase 0)
- Fabrication check: run §7.2's flagged-terms check across a batch of past `PipelineRun` artifacts — track flag rate over time as a leading indicator (rising flag rate = writer drifting toward over-specific framing)
- Self-consistency: run HM 3-5x on a fixed sample of past letters at the configured temperature, report score variance — establishes the **noise floor** before any redesign is judged

**Tier 2 — Pairwise variant comparison (when testing a specific change, e.g. "does the skill mapper help?"):**
- Take 10-15 past JDs, generate letters with variant A (current) and variant B (with skill mapper), same seed/temp where possible
- Either: (a) you blind-review pairs and pick a winner, or (b) have the HM do pairwise comparison (less reliable but free/automatable, useful for quick iteration before a human pass)
- Report win-rate of B over A — more interpretable than "B's average score was 0.3 higher" given 8B noise

**Tier 3 — Calibration check (periodic, low-frequency):**
- Every N real pipeline runs, log your own quick 1-5 "would I send this" rating alongside the HM's fit/quality scores
- Track correlation over time — if it drops after a prompt/config change, that change degraded judge alignment even if raw scores look fine

### Where this lives
Extends the `evals/` directory from the prior doc:
- `evals/golden_vault_dataset.yaml` (retrieval — already planned)
- `evals/golden_skill_map.yaml` (new — `{requirement, candidate_bullets, expected_framing}`)
- `evals/run_self_consistency.py` (new — Tier 1 noise-floor check)
- `evals/run_pairwise.py` (new — Tier 2, takes two pipeline configs + a job list, outputs win/loss/tie table)
- `evals/calibration_log.jsonl` (new — Tier 3, append-only log of `{run_id, hm_fit_score, hm_quality_score, your_rating, timestamp}`)

---

## 7. Critical gaps / open questions (things this doc doesn't fully resolve)

1. **Compounding 8B noise:** Skill Mapper + Fit-judge + Quality-judge are now three more places an 8B model's unreliability shows up, on top of Writer and the original reviewer. Worth considering whether *just the HM/fit-judge* (which now does more reasoning) warrants a larger local model even at a latency cost, since it runs ~4x per pipeline vs. the writer.
2. **Framing guardrail enforcement — the post-hoc fabrication check, in detail:**

   "Vocabulary change OK, new facts not OK" is easy to state and hard to enforce with an 8B model via prompting alone. Proposed deterministic (regex/lexical, no LLM) check that runs **after the writer drafts, before/alongside the HM pass**:

   **Step 1 — build the "allowed facts" set** from everything the writer was given:
   - Numbers: regex over matched `ExperienceHighlight.bullets`, vault excerpts, and skill_map `underlying_skill`/`framing_angle` text — `\b\d[\d,.]*%?\b` (catches "180ms", "50k", "12%", "3x", etc. with light normalization, e.g. strip commas).
   - Proper nouns / tech terms: rather than generic NER (noisy on tech jargon), extract from a **known vocabulary** — capitalized multi-word phrases and tech terms already present in the kit YAML + vault notes + `company_intelligence` (company name, product names). This is a closed-ish vocabulary since your kit/vault are small (~50 sections), so a simple capitalized-token extractor + frozenset lookup is tractable.

   **Step 2 — extract the same entity types from the writer's draft.**

   **Step 3 — diff:** any number or proper-noun/tech-term in the draft **not** in the allowed set → flag.

   **Step 4 — what to do with flags:** don't hard-block (false positives are likely — e.g. the writer correctly computes "60%" from "3 of 5" in the source, which is valid reasoning but the literal string "60%" wasn't present). Instead:
   - Pass `flagged_terms: list[str]` into the HM's input as an extra field: *"The following terms in the draft were not found verbatim in the source material — verify they are reasonable derivations, not fabrications: [...]"* — lets the 8B HM focus its (limited) reasoning on a short, specific list rather than re-deriving fact-checking from scratch.
   - Optionally surface `flagged_terms` in the Slack summary / `agent_log` for your own spot-check, regardless of HM verdict — this is cheap insurance against the compounding-noise problem in §7.1.

   This check is cheap (string ops on already-short text, no extra LLM call) and directly mitigates the highest-risk failure mode of the reframing design: the writer "filling in" a more specific-sounding number or tool name than the source actually supports.
3. **Skill map accuracy depends on `jd_requirements` accuracy** (researcher's extraction) — errors compound downstream into the fit-judge, which uses the *same* list. If the researcher mis-extracts requirements, both the skill-mapper and HM inherit that error with no cross-check.
4. **Two-threshold verdict creates more revise-loop edge cases:** what if fit improves but quality regresses across a revision (writer "fixes" framing but introduces redundancy)? Need a tie-breaking/regression-prevention rule (e.g., never accept a revision that drops quality_score by more than X even if fit improves) — otherwise the loop could oscillate.
5. **Genuine gaps feeding back upstream** (to job filtering/scoring) is noted as out-of-scope but is probably the highest-value long-term signal here — worth a follow-up design doc once this loop exists and you have data from it.
6. **Eval harness (from the prior doc) needs to expand**: Phase 0's golden dataset only covers retrieval. A skill_map and fit-judgment also need eval — likely a second small golden set: `{jd_requirements, expected_skill_map_mappings}` curated by you, since "is this framing reasonable" is fundamentally a judgment call only you can label well.
7. **Latency budget:** going from 3 LLM-call-types to potentially 5 (researcher, skill-mapper, writer, HM-fit, HM-quality — though §4 proposes combining the last two into one call) across up to 4 revision rounds is a meaningful increase in wall-clock time on local 8B inference. Worth measuring before committing to the full design.
