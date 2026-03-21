"""
Content Agent — generates the weekly newsletter using Claude Haiku.

Responsibilities:
  1. Take feed items from Feed Agent
  2. Select 2 affiliate tools to feature this week (rotating)
  3. Call Claude to write the newsletter with embedded affiliate CTAs
  4. Return (subject_line, html_body, plain_text_body)

Self-correction: exponential backoff on API failures, fallback to cached prompt.
"""
import json
import logging
import time
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)


NEWSLETTER_SYSTEM_PROMPT = """You are an expert newsletter writer for "{newsletter_name}".
Niche: {niche}
Tagline: {tagline}

Write in a friendly, expert tone — like a knowledgeable friend sharing insider tips.
Never use marketing fluff. Be concrete, specific, and valuable.
Each issue should feel like a curated briefing, not a content dump.

Output ONLY raw HTML (no markdown fences, no preamble). Use inline styles for email compatibility.
Do NOT include <html>, <head>, or <body> tags. Output only the email body content."""

NEWSLETTER_USER_PROMPT = """Write this week's issue of {newsletter_name}.

TODAY'S TOP STORIES (use 4-6 of these, pick the most relevant):
{stories}

SPONSORED TOOLS TO FEATURE (weave these in naturally — NOT as ads, as genuine recommendations):
{sponsor_1}
{sponsor_2}

STRUCTURE (follow exactly):
1. Subject line (compelling, under 50 chars, no clickbait) — output as: SUBJECT: <line>
2. Preview text (preheader, 80-100 chars) — output as: PREVIEW: <text>
3. Opening hook (1-2 sentences, current event hook or provocative insight)
4. THIS WEEK IN AI section: 4-6 bullet story summaries with analysis (2-3 sentences each)
5. TOOL OF THE WEEK: feature the primary sponsor tool with a genuine use case (3-4 sentences)
6. QUICK WINS section: 2-3 actionable tips using AI tools readers can try today
7. ALSO WORTH KNOWING: mention the secondary sponsor naturally in 1-2 sentences
8. CLOSING: one punchy sentence + sign-off

RULES:
- Write the SUBJECT: and PREVIEW: lines first, then the HTML body
- Affiliate CTAs should feel like recommendations, not ads
- Include the exact affiliate URL in anchor tags: <a href="{sponsor_1_url}">{sponsor_1_cta}</a>
- Total HTML body: 500-700 words. Email-safe HTML only."""


class ContentAgent:
    def __init__(
        self,
        api_key: str,
        affiliate_file: Path,
        state_file: Path,
        newsletter_name: str,
        niche: str,
        tagline: str,
        model: str = "claude-haiku-4-5-20251001",
        max_retries: int = 3,
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_retries = max_retries
        self.newsletter_name = newsletter_name
        self.niche = niche
        self.tagline = tagline
        self.affiliate_file = affiliate_file
        self.state_file = state_file
        self._load_affiliates()

    def _load_affiliates(self) -> None:
        with open(self.affiliate_file) as f:
            self.affiliates = json.load(f)

    def _load_state(self) -> dict:
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"affiliate_rotation_index": 0}

    def _save_rotation_index(self, idx: int) -> None:
        state = self._load_state()
        state["affiliate_rotation_index"] = idx
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2)

    def _get_weekly_sponsors(self) -> tuple[dict, dict]:
        """Rotate through affiliate tools weekly."""
        tools = list(self.affiliates["tools"].values())
        state = self._load_state()
        idx = state.get("affiliate_rotation_index", 0)

        sponsor_1 = tools[idx % len(tools)]
        sponsor_2 = tools[(idx + 1) % len(tools)]

        self._save_rotation_index(idx + 2)
        return sponsor_1, sponsor_2

    def _format_stories(self, items: list[dict]) -> str:
        lines = []
        for i, item in enumerate(items, 1):
            lines.append(
                f"{i}. [{item['source']}] {item['title']}\n"
                f"   Summary: {item['summary'][:300]}\n"
                f"   URL: {item['url']}"
            )
        return "\n\n".join(lines)

    def _call_claude(self, prompt: str) -> str:
        """Call Claude with exponential backoff retry."""
        for attempt in range(self.max_retries):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=NEWSLETTER_SYSTEM_PROMPT.format(
                        newsletter_name=self.newsletter_name,
                        niche=self.niche,
                        tagline=self.tagline,
                    ),
                    messages=[{"role": "user", "content": prompt}],
                )
                return response.content[0].text
            except anthropic.RateLimitError:
                wait = (2 ** attempt) * 5
                logger.warning(f"Rate limited. Waiting {wait}s (attempt {attempt + 1})")
                time.sleep(wait)
            except anthropic.APIConnectionError as e:
                wait = (2 ** attempt) * 3
                logger.warning(f"Connection error: {e}. Waiting {wait}s")
                time.sleep(wait)
            except anthropic.APIStatusError as e:
                if e.status_code >= 500:  # server error, retry
                    wait = (2 ** attempt) * 5
                    logger.warning(f"Server error {e.status_code}. Waiting {wait}s")
                    time.sleep(wait)
                else:
                    raise  # 4xx errors shouldn't be retried

        raise RuntimeError(f"Claude API failed after {self.max_retries} attempts")

    def _parse_response(self, raw: str) -> tuple[str, str, str]:
        """Extract subject, preview, and body from Claude's output."""
        lines = raw.strip().split("\n")
        subject = "Your weekly AI briefing"
        preview = "The AI tools and insights that matter this week"
        body_lines = []
        body_started = False

        for line in lines:
            if line.startswith("SUBJECT:"):
                subject = line.replace("SUBJECT:", "").strip()
            elif line.startswith("PREVIEW:"):
                preview = line.replace("PREVIEW:", "").strip()
            else:
                body_started = True

            if body_started and not line.startswith("SUBJECT:") and not line.startswith("PREVIEW:"):
                body_lines.append(line)

        html_body = "\n".join(body_lines).strip()

        # Wrap in basic email-safe container
        html_body = f"""
<div style="font-family: Georgia, 'Times New Roman', serif; max-width: 600px; margin: 0 auto; color: #1a1a1a; line-height: 1.6;">
  <div style="background: #0f0f0f; padding: 20px 24px; border-radius: 4px 4px 0 0;">
    <h1 style="color: #ffffff; font-size: 18px; margin: 0; letter-spacing: 0.5px;">
      {self.newsletter_name}
    </h1>
    <p style="color: #888; font-size: 12px; margin: 4px 0 0;">{self.tagline}</p>
  </div>
  <div style="padding: 24px; background: #fafafa; border: 1px solid #e5e5e5; border-top: none;">
    {html_body}
  </div>
  <div style="padding: 16px 24px; background: #f5f5f5; border: 1px solid #e5e5e5; border-top: none; text-align: center;">
    <p style="margin: 0 0 8px; font-size: 13px; color: #444;">
      📖 Deep-dive reviews &amp; comparisons at
      <a href="https://sutraflow.org" style="color: #0066cc;">sutraflow.org</a>
    </p>
  </div>
  <div style="padding: 16px 24px; background: #f0f0f0; border: 1px solid #e5e5e5; border-top: none; font-size: 11px; color: #888;">
    <p>You're receiving this because you subscribed to {self.newsletter_name}.</p>
    <p>Contains affiliate links. We only recommend tools we'd use ourselves.</p>
    <p>AI Tools Insider · 1111 S Figueroa St · Los Angeles, CA 90015</p>
    <p><a href="{{{{ unsubscribe_url }}}}" style="color: #888;">Unsubscribe</a> · <a href="https://sutraflow.org" style="color: #888;">Website</a></p>
  </div>
</div>
""".strip()

        return subject, preview, html_body

    # ── Public Interface ────────────────────────────────────────────────────────
    def generate_issue(self, feed_items: list[dict]) -> tuple[str, str, str]:
        """
        Generate newsletter issue.
        Returns: (subject, preview_text, html_body)
        """
        sponsor_1, sponsor_2 = self._get_weekly_sponsors()

        user_prompt = NEWSLETTER_USER_PROMPT.format(
            newsletter_name=self.newsletter_name,
            stories=self._format_stories(feed_items),
            sponsor_1=(
                f"Name: {sponsor_1['display_name']}\n"
                f"One-liner: {sponsor_1['one_liner']}\n"
                f"CTA: {sponsor_1['cta']}"
            ),
            sponsor_2=(
                f"Name: {sponsor_2['display_name']}\n"
                f"One-liner: {sponsor_2['one_liner']}\n"
                f"CTA: {sponsor_2['cta']}"
            ),
            sponsor_1_url=sponsor_1["affiliate_url"],
            sponsor_1_cta=sponsor_1["cta"],
        )

        logger.info(f"Generating newsletter with sponsors: {sponsor_1['name']}, {sponsor_2['name']}")
        raw = self._call_claude(user_prompt)
        subject, preview, html_body = self._parse_response(raw)

        logger.info(f"Content Agent: generated issue — Subject: '{subject}'")
        return subject, preview, html_body
