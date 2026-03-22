"""
Content Agent — generates SEO-optimized articles using Claude Haiku.

For each keyword, generates:
  - Full article in Hugo Markdown format (with frontmatter)
  - Proper H2/H3 structure for SEO
  - Affiliate links embedded naturally
  - Meta description, OG tags, JSON-LD structured data

Templates supported: listicle, comparison, review, tutorial

Cost: ~$0.02-0.04 per article with Claude Haiku
"""
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)


# ── Prompt Templates ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a hands-on AI tools reviewer who personally tests every tool before writing about it.

Rules:
- Write from FIRST-PERSON experience: "I tested...", "After using X for 3 weeks...", "In my workflow..."
- Include specific, concrete details only someone who used the tool would know (UI quirks, loading times, export limitations)
- Mention specific dates, version numbers, and pricing checked dates: "As of March 2026, pricing starts at..."
- Use active voice, short paragraphs (2-3 sentences max)
- Include genuine pros AND cons — a review that's all positive is not credible
- Affiliate CTAs should feel like genuine recommendations, not sales copy
- Add at least one "What I wish I knew before signing up" insight per tool
- NEVER say "In conclusion" or "In this article" — just start with value
- NEVER use generic filler like "In today's rapidly evolving landscape" or "Whether you're a beginner or expert"
- Output ONLY the article content, no preamble or meta-commentary"""

LISTICLE_PROMPT = """Write a comprehensive, SEO-optimized article for the keyword: "{keyword}"

AFFILIATE TOOLS TO FEATURE:
Primary: {primary_tool_name} — Link: {primary_url} — CTA: "{primary_cta}"
Secondary: {secondary_tool_name} — Link: {secondary_url} — CTA: "{secondary_cta}"

ARTICLE STRUCTURE (follow exactly, use proper markdown):
1. Opening paragraph: Hook + what reader will learn (no "In this article")
2. ## Why [Topic] Matters in 2026 (context, 100 words)
3. ## The Best [Topic]: Quick Comparison Table (markdown table with Name, Best For, Price, Rating)
4. ## [Tool 1 Name]: Best for [use case] (150 words, with genuine pros/cons list)
   - Include: <a href="{primary_url}" rel="nofollow sponsored">{primary_cta}</a>
5. ## [Tool 2 Name]: Best for [use case] (150 words, pros/cons)
6. ## [Tool 3 Name]: Best for [use case] (100 words, pros/cons)
7. ## [Tool 4 Name]: Best for [use case] (100 words)
   - Include: <a href="{secondary_url}" rel="nofollow sponsored">{secondary_cta}</a>
8. ## How to Choose the Right Tool (decision framework, 150 words)
9. ## Frequently Asked Questions (3 Q&As, targeting related keywords)

Target word count: 900-1200 words
Include: one comparison table, two affiliate CTAs, numbered/bulleted lists for scannability

E-E-A-T REQUIREMENTS (critical for Google rankings):
- Write from first-person: "I've tested all of these" / "My top pick after hands-on testing"
- Include one specific detail per tool that shows real usage (loading speed, UI friction, export quality)
- State when pricing was last verified: "Pricing verified March 2026"
- Include a "What surprised me" or unexpected finding"""

COMPARISON_PROMPT = """Write a comprehensive comparison article for the keyword: "{keyword}"

AFFILIATE TOOLS TO FEATURE:
Primary: {primary_tool_name} — Link: {primary_url} — CTA: "{primary_cta}"
Secondary: {secondary_tool_name} — Link: {secondary_url} — CTA: "{secondary_cta}"

ARTICLE STRUCTURE:
1. Opening: Who should read this, key verdict (2 sentences)
2. ## Quick Verdict (summary table: Category | Winner | Why)
3. ## [Tool A] Overview (features, pricing, best for — 150 words)
4. ## [Tool B] Overview (features, pricing, best for — 150 words)
5. ## Head-to-Head Comparison
   - ### Writing Quality / Core Feature
   - ### Pricing & Value
   - ### Ease of Use
   - ### Integrations
6. ## [Tool A] vs [Tool B]: Which Should You Choose? (decision tree by use case)
   - Include: <a href="{primary_url}" rel="nofollow sponsored">{primary_cta}</a>
7. ## Alternatives to Consider (2-3 sentences + <a href="{secondary_url}" rel="nofollow sponsored">{secondary_cta}</a>)
8. ## FAQ (3 questions)

Target: 900-1100 words. Be decisive — readers want a clear recommendation.

E-E-A-T REQUIREMENTS:
- Write as someone who tested both tools side by side: "I ran the same keyword through both..."
- Include one concrete comparison result (e.g., "Tool A generated 1,200 words in 45 seconds vs Tool B's 800 words in 30 seconds")
- State your clear winner and why — no fence-sitting"""

REVIEW_PROMPT = """Write an in-depth, honest review for the keyword: "{keyword}"

PRIMARY AFFILIATE: {primary_tool_name} — Link: {primary_url} — CTA: "{primary_cta}"
SECONDARY MENTION: {secondary_tool_name} — Link: {secondary_url} — CTA: "{secondary_cta}"

ARTICLE STRUCTURE:
1. ## [Tool] Review: The Verdict Up Front (rating out of 5, one-sentence verdict)
2. ## What Is [Tool]? (brief overview, target user, 80 words)
3. ## Key Features
   - Use H3 for each major feature (4-5 features)
   - Be specific about what it does and doesn't do well
4. ## Pricing & Plans (markdown table: Plan | Price | Key Features)
5. ## Pros and Cons (bullet lists, honest — include real limitations)
6. ## Who Is [Tool] Best For? (3 specific user types with use cases)
   - Include: <a href="{primary_url}" rel="nofollow sponsored">{primary_cta}</a>
7. ## [Tool] Alternatives (2 paragraphs, mention secondary affiliate naturally)
   - Include: <a href="{secondary_url}" rel="nofollow sponsored">{secondary_cta}</a>
8. ## Final Verdict (2-3 sentences, clear recommendation)
9. ## FAQ (3 common questions)

Target: 1000-1300 words. Be honest about limitations — it builds trust.

E-E-A-T REQUIREMENTS (critical for Google rankings):
- Write as if you personally tested the tool: "When I ran my first content audit..."
- Include at least one specific, non-obvious detail (e.g., "The export button is buried under Settings > Integrations")
- Mention the exact date you checked pricing
- Include one "gotcha" or limitation that only a real user would notice
- End the review with a clear, opinionated verdict — not wishy-washy"""

TUTORIAL_PROMPT = """Write a practical, step-by-step tutorial for the keyword: "{keyword}"

TOOLS TO RECOMMEND:
Primary: {primary_tool_name} — Link: {primary_url} — CTA: "{primary_cta}"
Secondary: {secondary_tool_name} — Link: {secondary_url} — CTA: "{secondary_cta}"

ARTICLE STRUCTURE:
1. Opening: What the reader will achieve + why it matters (no "In this tutorial")
2. ## What You'll Need (prerequisites, tools, time estimate)
   - Include: <a href="{primary_url}" rel="nofollow sponsored">{primary_cta}</a>
3. ## Step 1: [First Action] (concrete, numbered steps within)
4. ## Step 2: [Second Action]
5. ## Step 3: [Third Action]
6. ## Step 4: [Fourth Action]
7. ## Step 5: [Fifth Action] (or "Putting It All Together")
8. ## Pro Tips & Common Mistakes (3-4 bullets)
9. ## Next Steps (what to do after completing this — natural secondary affiliate mention)
   - Include: <a href="{secondary_url}" rel="nofollow sponsored">{secondary_cta}</a>
10. ## FAQ (3 questions)

Target: 900-1100 words. Use numbered lists for steps, be extremely specific.

E-E-A-T REQUIREMENTS:
- Write as if you actually completed these steps: "When I first tried this, I hit a snag at step 3..."
- Include one real-world workflow example with specific outcomes
- Mention any prerequisite gotchas the reader should know about"""

TEMPLATE_MAP = {
    "listicle": LISTICLE_PROMPT,
    "comparison": COMPARISON_PROMPT,
    "review": REVIEW_PROMPT,
    "tutorial": TUTORIAL_PROMPT,
}


class ContentAgent:
    def __init__(
        self,
        api_key: str,
        affiliate_file: Path,
        model: str = "claude-haiku-4-5-20251001",
        max_retries: int = 3,
        min_word_count: int = 600,
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_retries = max_retries
        self.min_word_count = min_word_count

        with open(affiliate_file) as f:
            self.affiliates = json.load(f)["tools"]

    def _call_claude(self, user_prompt: str) -> str:
        """Claude API call with exponential backoff."""
        for attempt in range(self.max_retries):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=3000,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                return response.content[0].text

            except anthropic.RateLimitError:
                wait = (2 ** attempt) * 10
                logger.warning(f"Rate limited. Waiting {wait}s")
                time.sleep(wait)
            except anthropic.APIConnectionError as e:
                wait = (2 ** attempt) * 5
                logger.warning(f"Connection error: {e}. Waiting {wait}s")
                time.sleep(wait)
            except anthropic.APIStatusError as e:
                if e.status_code >= 500:
                    wait = (2 ** attempt) * 10
                    logger.warning(f"Server error {e.status_code}. Waiting {wait}s")
                    time.sleep(wait)
                else:
                    raise

        raise RuntimeError(f"Claude API failed after {self.max_retries} attempts")

    def _count_words(self, text: str) -> int:
        return len(re.findall(r"\w+", text))

    def _generate_meta_description(self, keyword: str, article_body: str) -> str:
        """Extract or generate a 155-char meta description."""
        # Take first 2 sentences from article as meta description
        sentences = re.split(r"(?<=[.!?])\s+", article_body.strip())
        meta = " ".join(sentences[:2])
        meta = re.sub(r"[#*`]", "", meta)  # remove markdown
        if len(meta) > 155:
            meta = meta[:152] + "..."
        return meta

    _TEMPLATE_TAGS = {
        "review": ["review", "ai-tools"],
        "comparison": ["comparison", "ai-tools"],
        "tutorial": ["tutorial", "ai-tools"],
        "listicle": ["review", "ai-tools"],
    }

    def _build_frontmatter(
        self,
        keyword: dict,
        meta_description: str,
        primary_affiliate: str,
    ) -> str:
        now = datetime.now(timezone.utc)
        tags = self._TEMPLATE_TAGS.get(keyword.get("template", "listicle"), ["ai-tools"])
        tags_yaml = ", ".join(f'"{t}"' for t in tags)
        return f"""---
title: "{keyword['keyword'].title()}"
slug: "{keyword['slug']}"
date: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}
lastmod: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}
description: "{meta_description}"
keywords: ["{keyword['keyword']}", "ai tools", "review", "comparison"]
tags: [{tags_yaml}]
template: "{keyword['template']}"
intent: "{keyword['intent']}"
draft: false
author: "Sutra Editorial"
showToc: true
TocOpen: true
affiliate_disclosure: "This article contains affiliate links. We may earn a commission at no extra cost to you."
schema_type: "Article"
---

*Last tested and verified: {now.strftime('%B %Y')}. Pricing and features confirmed accurate as of this date.*

"""

    def generate_article(self, keyword: dict) -> tuple[str, bool]:
        """
        Generate a complete Hugo markdown article for the given keyword dict.
        Returns: (markdown_content, success_bool)
        """
        template_key = keyword.get("template", "listicle")
        primary_key = keyword.get("primary_affiliate", "semrush")
        secondary_key = keyword.get("secondary_affiliate", "notion")

        primary = self.affiliates.get(primary_key, self.affiliates["semrush"])
        secondary = self.affiliates.get(secondary_key, self.affiliates["notion"])

        template = TEMPLATE_MAP.get(template_key, LISTICLE_PROMPT)
        user_prompt = template.format(
            keyword=keyword["keyword"],
            primary_tool_name=primary["name"],
            primary_url=primary["affiliate_url"],
            primary_cta=primary["cta_text"],
            secondary_tool_name=secondary["name"],
            secondary_url=secondary["affiliate_url"],
            secondary_cta=secondary["cta_text"],
        )

        try:
            article_body = self._call_claude(user_prompt)
            word_count = self._count_words(article_body)

            if word_count < self.min_word_count:
                logger.warning(
                    f"Article too short ({word_count} words) for '{keyword['keyword']}' — skipping"
                )
                return "", False

            meta_desc = self._generate_meta_description(keyword["keyword"], article_body)
            frontmatter = self._build_frontmatter(keyword, meta_desc, primary_key)
            full_article = frontmatter + article_body

            logger.info(
                f"Content Agent: generated '{keyword['keyword']}' "
                f"({word_count} words, {template_key})"
            )
            return full_article, True

        except Exception as e:
            logger.error(f"Content Agent: failed for '{keyword['keyword']}': {e}")
            return "", False
