---
title: "feat: Content Quality Gate + E-E-A-T Hardening"
type: feat
status: completed
date: 2026-03-27
---

# Content Quality Gate + E-E-A-T Hardening

## Overview

Add a content quality scoring gate and E-E-A-T signals to the SEO content pipeline (`business2_seo`). Every article is scored by Claude before publishing. Articles below threshold get one auto-revision attempt, then are discarded. Published articles gain author bylines, source citations, review dates, and auto-generated internal links.

## Problem Statement

Google's December 2025 update penalized pure AI content farms by 40-60% traffic loss. Our articles have basic first-person framing from prompt engineering but lack:

- **No quality gate** — every generated article publishes, even low-quality ones
- **No structured E-E-A-T signals** — no author page, no "reviewed by", no source citations, no schema markup beyond generic `Article`
- **No internal linking** — 9 articles with zero cross-links, hurting topical authority
- **No quality tracking** — no data on article quality trends over time
- **Latent bug** — `_deprecated_copy_ai` string in affiliate_links.json will crash content generation for any keyword with `primary_affiliate: copy_ai`

## Proposed Solution

### 1. Quality Scoring Agent (`quality_agent.py`)

New agent inserted between ContentAgent and PublisherAgent in the orchestrator.

**Scoring criteria (1-10 each):**

| Criteria | What it checks |
|----------|----------------|
| Structure | Proper H2/H3 hierarchy, comparison table present (per template), FAQ section |
| E-E-A-T | First-person language count, specific dates/prices, pros AND cons, "tested/verified" |
| SEO | Keyword in first H2, meta description 120-155 chars, keyword appears in intro |
| Readability | Paragraph length (<4 sentences), no filler phrases, active voice |
| Affiliate | Correct affiliate URLs present, not pointing to deprecated tools |
| Originality | No generic opening phrases, specific details per tool |

**Scoring method:** Single Claude Haiku call with structured JSON output. The scoring prompt provides the article text + rubric → Claude returns `{"structure": 8, "eeat": 7, ...}` with per-criterion scores and a brief reason for each.

**Thresholds:**
- All criteria >= 7/10 → publish
- Any criteria < 7 → auto-revise once (send article + failing criteria back to ContentAgent)
- Still failing after revision → reject, log to `data/quality_log.json`, move keyword to `failed`

**Cost:** ~$0.01 per scoring call (Haiku). Revision adds one more generation call (~$0.03). Total pipeline cost increase: ~15-25%.

### 2. E-E-A-T Frontmatter Enhancements

Update `content_agent.py` `_build_frontmatter()` to add:

```yaml
author: "Sutra Editorial"
author_url: "/about/"
reviewed_by: "Editorial Team"
last_fact_checked: "2026-03-27"
sources:
  - "Official pricing page (verified March 2026)"
schema_type: "Review"  # for review/comparison articles (enables rich snippets)
```

### 3. Author Page

Create `business2_seo/hugo_site/content/about.md` — a static page establishing editorial credibility:
- Who writes the reviews (editorial team persona)
- Testing methodology description
- Review process (tested → scored → fact-checked → published)
- Contact info

### 4. Internal Linking Agent (`internal_linker.py`)

Post-generation step that scans the article for mentions of topics covered by other published articles and inserts contextual links.

**Method:**
1. Load all published article slugs + titles + keywords from Hugo content dir
2. For each new article, find keyword/title overlaps with existing articles
3. Insert markdown links at first mention (e.g., "AI writing tools" → `[AI writing tools](/posts/best-ai-seo-tools-2026/)`)
4. Maximum 3-5 internal links per article to avoid over-linking

### 5. Quality Metrics Tracking

Append to `data/quality_log.json` after each run:

```json
{
  "date": "2026-03-27",
  "articles_generated": 8,
  "articles_passed": 6,
  "articles_revised": 2,
  "articles_rejected": 0,
  "avg_scores": {"structure": 8.1, "eeat": 7.4, "seo": 8.0, ...},
  "rejected_keywords": []
}
```

### 6. Fix Deprecated Affiliate Bug

Filter out `_deprecated_*` keys in ContentAgent's affiliate loading so they can't crash the pipeline.

## Implementation

### Phase 1: Foundation

- [x] Create `business2_seo/agents/quality_agent.py` with `QualityAgent` class
  - `score_article(content: str, keyword: dict) -> dict` — returns scores + pass/fail
  - `_build_scoring_prompt(content, keyword, template)` — structured JSON output prompt
  - Scoring rubric as class constant
- [x] Fix deprecated affiliate bug in `content_agent.py` — filter `_deprecated_*` keys during affiliate loading
- [x] Add `QUALITY_THRESHOLD` (default 7), `QUALITY_LOG_FILE` to `config.py`

### Phase 2: Pipeline Integration

- [x] Update `orchestrator.py` — insert quality gate between generation and publishing:
  ```
  generate_article() → score_article() → [pass? publish : revise → re-score → publish or reject]
  ```
- [x] Add revision flow to `content_agent.py`:
  - New method `revise_article(content: str, feedback: dict) -> str`
  - Takes original article + scoring feedback, asks Claude to fix specific issues
- [x] Update keyword_agent to support `failed` status for rejected articles

### Phase 3: E-E-A-T Signals

- [x] Update `_build_frontmatter()` in `content_agent.py`:
  - Add `author_url`, `reviewed_by`, `last_fact_checked`, `sources`
  - Use `schema_type: "Review"` for review/comparison templates
- [x] Create `business2_seo/hugo_site/content/about.md` — author/methodology page
- [x] Update SYSTEM_PROMPT to instruct Claude to include source citations in article body

### Phase 4: Internal Linking

- [x] Create `business2_seo/agents/internal_linker.py`
  - `add_internal_links(article_content: str, all_articles: list[dict]) -> str`
  - Scans for topic mentions, inserts contextual markdown links
  - Max 3-5 internal links per article
- [x] Integrate into orchestrator after quality gate, before publishing

### Phase 5: Quality Metrics + Testing

- [x] Implement quality log writing to `data/quality_log.json`
- [x] Test full pipeline with mock data: generate → score → revise → re-score → publish
- [x] Test rejection flow: article fails twice → logged + keyword marked failed
- [x] Verify E-E-A-T frontmatter renders correctly in Hugo/PaperMod

## Acceptance Criteria

- [ ] Articles scoring <7 on any criterion are auto-revised once before publishing
- [ ] Articles failing twice are rejected and logged (never published)
- [ ] All new articles include author, reviewed_by, last_fact_checked, sources in frontmatter
- [ ] Review/comparison articles use `schema_type: "Review"` for rich snippets
- [ ] /about/ page exists with editorial methodology
- [ ] New articles contain 3-5 internal links to related published content
- [ ] Quality scores logged per-run to quality_log.json
- [ ] Deprecated affiliate keys can't crash the pipeline
- [ ] Pipeline cost increase stays under 30% ($0.01 scoring + occasional $0.03 revision)

## Key Files

| File | Action |
|------|--------|
| `business2_seo/agents/quality_agent.py` | NEW — scoring + quality gate |
| `business2_seo/agents/internal_linker.py` | NEW — auto internal links |
| `business2_seo/agents/content_agent.py` | MODIFY — E-E-A-T frontmatter, revision method, fix affiliate bug |
| `business2_seo/orchestrator.py` | MODIFY — insert quality gate + internal linker steps |
| `business2_seo/config.py` | MODIFY — add quality threshold + log file config |
| `business2_seo/hugo_site/content/about.md` | NEW — author/methodology page |
| `business2_seo/data/quality_log.json` | NEW — quality metrics tracking |

## Sources & References

- Existing pipeline: `business2_seo/orchestrator.py:101-121` (generate → publish loop)
- Content generation: `business2_seo/agents/content_agent.py` (prompts, frontmatter builder)
- Publisher: `business2_seo/agents/publisher_agent.py` (writes markdown to disk)
- Affiliate config: `business2_seo/data/affiliate_links.json` (deprecated keys bug)
- Hugo config: `business2_seo/hugo_site/config.toml` (PaperMod theme)
- Google E-E-A-T guidelines: Experience, Expertise, Authoritativeness, Trustworthiness
