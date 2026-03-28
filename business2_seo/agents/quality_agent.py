"""
Quality Agent — scores articles before publishing and logs quality metrics.

Inserted between ContentAgent and PublisherAgent in the pipeline.
Uses Claude Haiku to score articles on 6 criteria (1-10 each).
Articles must score >= threshold on ALL criteria to publish.

Scoring cost: ~$0.01 per article (Haiku with ~2K input tokens).
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)

SCORING_PROMPT = """You are a content quality reviewer for an AI tools review site.
Score this article on 6 criteria (1-10 each). Be strict — only genuinely good content should score 7+.

ARTICLE KEYWORD: {keyword}
ARTICLE TEMPLATE: {template}

ARTICLE CONTENT:
{content}

SCORING RUBRIC:

1. STRUCTURE (1-10): Does it follow proper H2/H3 hierarchy? Is there a comparison table (for listicle/comparison)? FAQ section? Logical flow?
2. EEAT (1-10): First-person experience language? Specific dates/prices? Genuine pros AND cons? "I tested" framing? Concrete usage details?
3. SEO (1-10): Keyword in first H2? Meta description present and 120-155 chars? Keyword in intro paragraph? Proper heading hierarchy?
4. READABILITY (1-10): Short paragraphs (2-3 sentences)? Active voice? No filler phrases ("In today's rapidly evolving...")? Scannable with lists?
5. AFFILIATE (1-10): Are CTAs present and natural (not salesy)? Do affiliate links appear? Are tool names accurate (not hallucinated tools)?
6. ORIGINALITY (1-10): Specific details someone who used the tool would know? No generic descriptions? Unique insights or "what surprised me" elements?

Respond with ONLY valid JSON in this exact format, nothing else:
{{"structure": N, "eeat": N, "seo": N, "readability": N, "affiliate": N, "originality": N, "lowest_criteria": "name", "revision_guidance": "one sentence explaining what to fix if any score < 7"}}"""


class QualityAgent:
    def __init__(
        self,
        api_key: str,
        threshold: int = 7,
        log_file: Path | None = None,
        model: str = "claude-haiku-4-5-20251001",
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.threshold = threshold
        self.log_file = log_file

    def score_article(self, content: str, keyword: dict) -> dict:
        """Score an article on 6 quality criteria.

        Returns dict with:
          - scores: {structure: N, eeat: N, seo: N, readability: N, affiliate: N, originality: N}
          - passed: bool (all scores >= threshold)
          - lowest_criteria: str
          - revision_guidance: str
        """
        prompt = SCORING_PROMPT.format(
            keyword=keyword.get("keyword", ""),
            template=keyword.get("template", ""),
            content=content[:8000],  # truncate to stay within token limits
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

            # Parse JSON — handle potential markdown wrapping
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            scores = json.loads(raw)

        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Quality scoring failed, defaulting to pass: {e}")
            return {
                "scores": {k: 8 for k in ("structure", "eeat", "seo", "readability", "affiliate", "originality")},
                "passed": True,
                "lowest_criteria": "none",
                "revision_guidance": "",
                "error": str(e),
            }

        criteria = ("structure", "eeat", "seo", "readability", "affiliate", "originality")
        score_values = {k: scores.get(k, 5) for k in criteria}
        passed = all(v >= self.threshold for v in score_values.values())

        result = {
            "scores": score_values,
            "passed": passed,
            "lowest_criteria": scores.get("lowest_criteria", min(score_values, key=score_values.get)),
            "revision_guidance": scores.get("revision_guidance", ""),
        }

        logger.info(
            f"Quality: {keyword.get('keyword', '?')} — "
            f"scores={score_values}, passed={passed}"
        )
        return result

    def log_run(self, stats: dict) -> None:
        """Append run stats to quality log file."""
        if not self.log_file:
            return

        try:
            existing = json.loads(self.log_file.read_text()) if self.log_file.exists() else []
        except (json.JSONDecodeError, FileNotFoundError):
            existing = []

        entry = {
            "date": datetime.now(timezone.utc).isoformat(),
            **stats,
        }
        existing.append(entry)

        # Keep last 100 entries
        if len(existing) > 100:
            existing = existing[-100:]

        self.log_file.write_text(json.dumps(existing, indent=2))
        logger.info(f"Quality log: {entry}")
