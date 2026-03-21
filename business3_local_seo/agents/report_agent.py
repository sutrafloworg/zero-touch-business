"""
Report Agent — generates personalized Local SEO audit PDFs using Claude.

For each business with a rank drop, generates a 1-page PDF report:
  - What happened (rank change)
  - Why it happened (specific reasons from Analyzer)
  - What to fix (actionable recommendations from Claude)
  - CTA to learn more / get a full audit

Uses fpdf2 for PDF generation — pure Python, no system deps.
"""
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from fpdf import FPDF

logger = logging.getLogger(__name__)

AUDIT_PROMPT = """You are a local SEO expert writing a personalized audit for a business owner.

BUSINESS: {business_name}
CATEGORY: {category}
CITY: {city}
PREVIOUS RANK: #{prev_rank} in Google Maps
CURRENT RANK: #{curr_rank} in Google Maps (dropped {rank_change} positions)
RATING: {rating} stars ({reviews} reviews)
DETECTED ISSUES:
{reasons}

Write a SHORT, actionable audit (250 words max). Use this structure:

1. WHAT HAPPENED — one sentence stating the rank drop factually
2. WHY — 2-3 bullet points explaining the likely causes (use the detected issues above)
3. QUICK WINS — 3 specific, actionable steps they can take THIS WEEK to recover:
   - Be concrete: "Ask your 3 most recent customers for a Google review" not "get more reviews"
   - Include one tip about Google Business Profile optimization
   - Include one tip about their website (if they have one) or getting one (if they don't)

Tone: direct, helpful, no jargon. Like a knowledgeable friend texting advice.
Do NOT use markdown formatting — output plain text only."""


class ReportAgent:
    def __init__(
        self,
        api_key: str,
        reports_dir: Path,
        model: str = "claude-haiku-4-5-20251001",
        max_retries: int = 3,
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.reports_dir = reports_dir
        self.reports_dir.mkdir(exist_ok=True)
        self.max_retries = max_retries

    def _call_claude(self, prompt: str) -> str:
        for attempt in range(self.max_retries):
            try:
                resp = self.client.messages.create(
                    model=self.model,
                    max_tokens=1024,
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.content[0].text
            except Exception as e:
                wait = (2 ** attempt) * 5
                logger.warning(f"Claude API error (attempt {attempt + 1}): {e}. Waiting {wait}s")
                time.sleep(wait)
        raise RuntimeError("Claude API failed after retries")

    def generate_audit(self, alert: dict) -> Path | None:
        """
        Generate a PDF audit report for a single rank-drop alert.
        Returns path to generated PDF, or None on failure.
        """
        # Parse category key
        parts = alert["category_key"].split("_")
        city = parts[0].title() if parts else "Unknown"
        state = parts[1].upper() if len(parts) > 1 else ""
        category = parts[2].title() if len(parts) > 2 else "Business"

        reasons_text = "\n".join(f"- {r}" for r in alert.get("reasons", []))

        prompt = AUDIT_PROMPT.format(
            business_name=alert["business_name"],
            category=category,
            city=f"{city}, {state}",
            prev_rank=alert["prev_rank"],
            curr_rank=alert["curr_rank"],
            rank_change=alert["rank_change"],
            rating=alert.get("rating", "N/A"),
            reviews=alert.get("reviews", 0),
            reasons=reasons_text,
        )

        try:
            audit_text = self._call_claude(prompt)
        except Exception as e:
            logger.error(f"Report Agent: Claude failed for {alert['business_name']}: {e}")
            return None

        # Generate PDF
        return self._build_pdf(alert, audit_text, city, state, category)

    def _build_pdf(
        self,
        alert: dict,
        audit_text: str,
        city: str,
        state: str,
        category: str,
    ) -> Path:
        """Build a clean, branded PDF audit report."""
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=20)

        # Header bar
        pdf.set_fill_color(15, 15, 15)
        pdf.rect(0, 0, 210, 35, "F")
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 16)
        pdf.set_xy(15, 10)
        pdf.cell(0, 10, "LocalRank Sentinel", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_xy(15, 20)
        pdf.cell(0, 5, f"Local SEO Audit Report  |  {datetime.now().strftime('%B %d, %Y')}")

        # Business name
        pdf.set_text_color(0, 0, 0)
        pdf.set_xy(15, 45)
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 8, alert["business_name"], new_x="LMARGIN", new_y="NEXT")

        # Category + location
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 6, f"{category} in {city}, {state}", new_x="LMARGIN", new_y="NEXT")

        # Rank change highlight box
        pdf.ln(6)
        pdf.set_fill_color(255, 243, 243)
        pdf.set_draw_color(239, 68, 68)
        rank_text = f"Rank dropped: #{alert['prev_rank']}  ->  #{alert['curr_rank']}  ({alert['rank_change']} positions lost)"
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(180, 30, 30)
        pdf.set_x(15)
        pdf.cell(180, 12, rank_text, border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")

        # Stats row
        pdf.ln(6)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 10)
        stats = f"Rating: {alert.get('rating', 'N/A')} stars  |  Reviews: {alert.get('reviews', 0)}  |  Previous reviews: {alert.get('prev_reviews', 0)}"
        pdf.cell(0, 6, stats, new_x="LMARGIN", new_y="NEXT")

        # Audit content
        pdf.ln(6)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(30, 30, 30)

        for line in audit_text.split("\n"):
            line = line.strip()
            if not line:
                pdf.ln(3)
                continue

            # Bold section headers
            if line.upper().startswith(("WHAT HAPPENED", "WHY", "QUICK WINS")):
                pdf.ln(3)
                pdf.set_font("Helvetica", "B", 11)
                pdf.set_text_color(0, 0, 0)
                pdf.multi_cell(180, 6, line)
                pdf.set_font("Helvetica", "", 10)
                pdf.set_text_color(30, 30, 30)
            else:
                pdf.multi_cell(180, 5, line)

        # Footer CTA
        pdf.ln(10)
        pdf.set_fill_color(15, 15, 15)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_x(15)
        pdf.cell(180, 10, "Want weekly monitoring? Reply to this email for details.", fill=True, align="C", new_x="LMARGIN", new_y="NEXT")

        pdf.set_text_color(120, 120, 120)
        pdf.set_font("Helvetica", "", 8)
        pdf.ln(4)
        pdf.cell(0, 5, "Generated by LocalRank Sentinel  |  sutraflow.org", align="C")

        # Save PDF
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "" for c in alert["business_name"])
        safe_name = safe_name.replace(" ", "-").lower()[:50]
        date_str = datetime.now().strftime("%Y%m%d")
        filename = f"audit_{safe_name}_{date_str}.pdf"
        filepath = self.reports_dir / filename
        pdf.output(str(filepath))

        logger.info(f"Report Agent: generated {filepath}")
        return filepath

    def generate_batch(self, alerts: list[dict]) -> list[dict]:
        """
        Generate PDFs for all alerts.
        Returns list of {alert, pdf_path} dicts.
        """
        results = []
        for alert in alerts:
            pdf_path = self.generate_audit(alert)
            if pdf_path:
                results.append({"alert": alert, "pdf_path": pdf_path})
            time.sleep(1)  # rate limit

        logger.info(f"Report Agent: generated {len(results)}/{len(alerts)} audit PDFs")
        return results
