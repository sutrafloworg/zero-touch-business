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
WEEKS TRACKED: {weeks_tracked}
DETECTED ISSUES:
{reasons}
{insights_text}

Write a SHORT, actionable audit (300 words max). Use this structure:

1. WHAT HAPPENED — one sentence stating the rank drop factually
2. WHY — 2-3 bullet points explaining the likely causes (use the detected issues above)
3. QUICK WINS — 3 specific, actionable steps they can take THIS WEEK to recover:
   - Be concrete: "Ask your 3 most recent customers for a Google review" not "get more reviews"
   - Include one tip about Google Business Profile optimization
   - Include one tip about their website (if they have one) or getting one (if they don't)

{insights_section}

Tone: direct, helpful, no jargon. Like a knowledgeable friend texting advice.
Do NOT use markdown formatting — output plain text only."""


def _format_insights_for_prompt(alert: dict) -> tuple[str, str]:
    """Format progressive insights into text for the Claude prompt.
    Returns (insights_text, insights_section) — extra data and extra instructions."""
    insights = alert.get("insights", {})
    weeks = alert.get("weeks_tracked", 1)
    text_parts = []
    section_parts = []

    if "review_velocity" in insights:
        rv = insights["review_velocity"]
        text_parts.append(
            f"REVIEW VELOCITY: {rv['reviews_per_week']} reviews/week over {rv['over_weeks']} weeks "
            f"({rv['verdict']}). Total gained: {rv['total_gained']}."
        )

    if "rank_trend" in insights:
        rt = insights["rank_trend"]
        text_parts.append(
            f"RANK TREND: {rt['direction']} over last {len(rt['history'])} weeks. "
            f"Best: #{rt['best_rank']}, Worst: #{rt['worst_rank']}. "
            f"Weekly positions: {', '.join(f'#{r}' for r in rt['history'])}"
        )
        section_parts.append(
            "4. YOUR TREND — one sentence summarizing whether their ranking is improving, "
            "declining, or volatile based on the rank history data."
        )

    if "competitor_spotlight" in insights:
        cs = insights["competitor_spotlight"]
        text_parts.append(
            f"COMPETITOR SPOTLIGHT: {cs['fastest_climber']} climbed {cs['climbed_positions']} positions "
            f"(now #{cs['their_current_rank']}), gained {cs['their_review_gain']} reviews, "
            f"rated {cs['their_rating']} stars."
        )
        section_parts.append(
            "5. COMPETITOR TO WATCH — one sentence about who is climbing fastest and what "
            "they are doing differently (based on competitor spotlight data)."
        )

    if "category_health" in insights:
        ch = insights["category_health"]
        text_parts.append(
            f"CATEGORY HEALTH SCORE: {ch['score']}/10 ({ch['position_summary']}). "
            f"Your reviews: {ch['your_reviews']} vs category avg: {ch['category_avg_reviews']}. "
            f"Your rating: {ch['your_rating']} vs category avg: {ch['category_avg_rating']}."
        )
        section_parts.append(
            "6. YOUR STANDING — one sentence giving their health score (X/10) and what it means "
            "relative to their local competition."
        )

    insights_text = "\n".join(text_parts) if text_parts else ""
    insights_section = "\n".join(section_parts) if section_parts else ""
    return insights_text, insights_section


def _sanitize_for_pdf(text: str) -> str:
    """Replace Unicode characters that Helvetica/latin-1 can't render."""
    replacements = {
        "\u2014": "--",   # em dash
        "\u2013": "-",    # en dash
        "\u2018": "'",    # left single quote
        "\u2019": "'",    # right single quote
        "\u201c": '"',    # left double quote
        "\u201d": '"',    # right double quote
        "\u2026": "...",  # ellipsis
        "\u2022": "-",    # bullet
        "\u00a0": " ",    # non-breaking space
        "\u2192": "->",   # right arrow
        "\u2190": "<-",   # left arrow
        "\u2023": ">",    # triangular bullet
        "\u25cf": "*",    # black circle
        "\u2605": "*",    # star
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    # Fallback: strip any remaining non-latin-1 characters
    return text.encode("latin-1", errors="replace").decode("latin-1")


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
        insights_text, insights_section = _format_insights_for_prompt(alert)

        prompt = AUDIT_PROMPT.format(
            business_name=alert["business_name"],
            category=category,
            city=f"{city}, {state}",
            prev_rank=alert["prev_rank"],
            curr_rank=alert["curr_rank"],
            rank_change=alert["rank_change"],
            rating=alert.get("rating", "N/A"),
            reviews=alert.get("reviews", 0),
            weeks_tracked=alert.get("weeks_tracked", 1),
            reasons=reasons_text,
            insights_text=insights_text,
            insights_section=insights_section,
        )

        try:
            audit_text = self._call_claude(prompt)
            audit_text = _sanitize_for_pdf(audit_text)
        except Exception as e:
            logger.error(f"Report Agent: Claude failed for {alert['business_name']}: {e}")
            return None

        # Generate PDF
        return self._build_pdf(alert, audit_text, city, state, category)

    # ── PDF color constants ────────────────────────────────────────────────
    BLACK = (15, 15, 15)
    WHITE = (255, 255, 255)
    DARK_GRAY = (50, 50, 50)
    MID_GRAY = (100, 100, 100)
    LIGHT_GRAY = (200, 200, 200)
    ACCENT = (0, 102, 204)        # professional blue
    RED_TEXT = (180, 30, 30)
    RED_BG = (255, 243, 243)
    GREEN_TEXT = (30, 130, 50)
    GREEN_BG = (240, 255, 244)
    SECTION_BG = (247, 248, 250)  # light gray for card backgrounds

    def _build_pdf(
        self,
        alert: dict,
        audit_text: str,
        city: str,
        state: str,
        category: str,
    ) -> Path:
        """Build a professional multi-page PDF audit report."""
        city = _sanitize_for_pdf(city)
        state = _sanitize_for_pdf(state)
        category = _sanitize_for_pdf(category)
        alert_safe = {}
        for k, v in alert.items():
            if isinstance(v, str):
                alert_safe[k] = _sanitize_for_pdf(v)
            else:
                alert_safe[k] = v
        alert = alert_safe
        insights = alert.get("insights", {})
        weeks = alert.get("weeks_tracked", 1)
        report_date = datetime.now().strftime("%B %d, %Y")

        pdf = FPDF()
        pdf.set_left_margin(20)
        pdf.set_right_margin(20)
        pdf.set_auto_page_break(auto=True, margin=25)
        w = 170  # content width = 210 - 20 - 20

        # ═══════════════════════════════════════════════════════════════════
        # PAGE 1 — COVER
        # ═══════════════════════════════════════════════════════════════════
        pdf.add_page()

        # Full-width dark header block
        pdf.set_fill_color(*self.BLACK)
        pdf.rect(0, 0, 210, 80, "F")

        # Accent stripe
        pdf.set_fill_color(*self.ACCENT)
        pdf.rect(0, 80, 210, 3, "F")

        # Brand name
        pdf.set_text_color(*self.WHITE)
        pdf.set_font("Helvetica", "B", 28)
        pdf.set_xy(20, 18)
        pdf.cell(w, 12, "LocalRank Sentinel", new_x="LMARGIN", new_y="NEXT")

        # Tagline
        pdf.set_font("Helvetica", "", 12)
        pdf.set_xy(20, 35)
        pdf.set_text_color(180, 190, 200)
        pdf.cell(w, 7, "Local SEO Intelligence & Monitoring", new_x="LMARGIN", new_y="NEXT")

        # Report type
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_xy(20, 52)
        pdf.set_text_color(*self.WHITE)
        pdf.cell(w, 8, "Ranking Audit Report", new_x="LMARGIN", new_y="NEXT")

        # Date
        pdf.set_font("Helvetica", "", 10)
        pdf.set_xy(20, 64)
        pdf.set_text_color(160, 170, 180)
        pdf.cell(w, 6, report_date, new_x="LMARGIN", new_y="NEXT")

        # ── Cover body: business details ──
        pdf.set_xy(20, 100)
        pdf.set_text_color(*self.DARK_GRAY)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(w, 6, "PREPARED FOR", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 22)
        pdf.set_text_color(*self.BLACK)
        pdf.multi_cell(w, 10, alert["business_name"], new_x="LMARGIN", new_y="NEXT")

        pdf.ln(4)
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(*self.MID_GRAY)
        location_str = f"{category}  |  {city}, {state}"
        pdf.cell(w, 7, location_str, new_x="LMARGIN", new_y="NEXT")

        # ── Key metrics cards ──
        pdf.ln(12)
        self._metric_card_row(pdf, w, [
            ("Current Rank", f"#{alert['curr_rank']}", self.RED_TEXT),
            ("Previous Rank", f"#{alert['prev_rank']}", self.DARK_GRAY),
            ("Positions Lost", str(alert["rank_change"]), self.RED_TEXT),
        ])

        pdf.ln(8)
        self._metric_card_row(pdf, w, [
            ("Rating", f"{alert.get('rating', 'N/A')} stars", self.DARK_GRAY),
            ("Reviews", str(alert.get("reviews", 0)), self.DARK_GRAY),
            ("Weeks Tracked", str(weeks), self.ACCENT),
        ])

        # ── Bottom status bar ──
        pdf.ln(12)
        if weeks > 1 and insights:
            badge_parts = [f"Week {weeks} of monitoring"]
            if "review_velocity" in insights:
                badge_parts.append(f"{insights['review_velocity']['reviews_per_week']} reviews/week")
            if "rank_trend" in insights:
                badge_parts.append(f"Trend: {insights['rank_trend']['direction']}")
            if "category_health" in insights:
                badge_parts.append(f"Health score: {insights['category_health']['score']}/10")
            pdf.set_fill_color(*self.SECTION_BG)
            pdf.set_draw_color(*self.LIGHT_GRAY)
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(*self.MID_GRAY)
            pdf.cell(w, 10, "   ".join(badge_parts), border=1, fill=True, align="C",
                     new_x="LMARGIN", new_y="NEXT")

        # Confidentiality note
        pdf.set_y(-40)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(*self.LIGHT_GRAY)
        pdf.cell(w, 5, "CONFIDENTIAL -- Prepared exclusively for the business named above.",
                 align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(w, 5, "sutraflow.org", align="C")

        # ═══════════════════════════════════════════════════════════════════
        # PAGE 2 — EXECUTIVE SUMMARY & ANALYSIS
        # ═══════════════════════════════════════════════════════════════════
        pdf.add_page()
        self._page_header(pdf, w, "Executive Summary", report_date)

        # Parse audit text into sections
        sections = self._parse_audit_sections(audit_text)

        # What Happened — alert box
        if "what happened" in sections:
            pdf.ln(4)
            pdf.set_fill_color(*self.RED_BG)
            pdf.set_draw_color(239, 68, 68)
            pdf.set_text_color(*self.RED_TEXT)
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(w, 8, "  ALERT: RANKING DECLINE DETECTED", fill=True, border=1,
                     new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(*self.DARK_GRAY)
            pdf.set_font("Helvetica", "", 10)
            pdf.ln(2)
            for line in sections["what happened"]:
                if line.strip():
                    pdf.multi_cell(w, 5.5, line.strip(), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)

        # Why — root cause analysis
        if "why" in sections:
            self._section_heading(pdf, w, "Root Cause Analysis")
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(*self.DARK_GRAY)
            for line in sections["why"]:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("-"):
                    # Bullet point with indent
                    pdf.set_x(25)
                    pdf.multi_cell(w - 5, 5.5, line, new_x="LMARGIN", new_y="NEXT")
                else:
                    pdf.multi_cell(w, 5.5, line, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)

        # Quick Wins — numbered action items in a card
        if "quick wins" in sections:
            self._section_heading(pdf, w, "Recommended Actions")
            pdf.set_fill_color(*self.SECTION_BG)
            pdf.set_draw_color(*self.LIGHT_GRAY)
            card_y = pdf.get_y()
            # Draw background card
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(*self.DARK_GRAY)
            for line in sections["quick wins"]:
                line = line.strip()
                if not line:
                    continue
                pdf.set_x(25)
                pdf.multi_cell(w - 5, 5.5, line, new_x="LMARGIN", new_y="NEXT")
                pdf.ln(2)
            pdf.ln(2)

        # ═══════════════════════════════════════════════════════════════════
        # PAGE 3 — PROGRESSIVE INSIGHTS (if available)
        # ═══════════════════════════════════════════════════════════════════
        has_insights = any(k in insights for k in
                          ("review_velocity", "rank_trend", "competitor_spotlight", "category_health"))

        if has_insights or weeks > 1:
            pdf.add_page()
            self._page_header(pdf, w, "Performance Intelligence", report_date)

            # Review Velocity
            if "review_velocity" in insights:
                rv = insights["review_velocity"]
                self._section_heading(pdf, w, "Review Velocity Analysis")
                color = self.GREEN_TEXT if rv["verdict"] == "strong" else self.RED_TEXT if rv["verdict"] == "stagnant" else self.DARK_GRAY
                self._metric_card_row(pdf, w, [
                    ("Reviews/Week", str(rv["reviews_per_week"]), color),
                    ("Total Gained", str(rv["total_gained"]), self.DARK_GRAY),
                    ("Status", rv["verdict"].upper(), color),
                ])
                pdf.ln(3)
                if rv["verdict"] == "stagnant":
                    pdf.set_font("Helvetica", "", 10)
                    pdf.set_text_color(*self.DARK_GRAY)
                    pdf.multi_cell(w, 5.5,
                        "Your review growth has stalled. Businesses averaging 3+ reviews/week "
                        "consistently outrank those below 1/week. Consider implementing a review "
                        "request workflow via text message after each service completion.",
                        new_x="LMARGIN", new_y="NEXT")
                elif rv["verdict"] == "strong":
                    pdf.set_font("Helvetica", "", 10)
                    pdf.set_text_color(*self.DARK_GRAY)
                    pdf.multi_cell(w, 5.5,
                        "Strong review velocity. You are building social proof faster than most "
                        "competitors. Maintain this pace -- it is one of the top 3 local ranking factors.",
                        new_x="LMARGIN", new_y="NEXT")
                pdf.ln(6)

            # Rank Trend
            if "rank_trend" in insights:
                rt = insights["rank_trend"]
                self._section_heading(pdf, w, "Ranking Trend")

                # Text-based rank history visualization
                history = rt.get("history", [])
                if history:
                    pdf.set_fill_color(*self.SECTION_BG)
                    pdf.set_draw_color(*self.LIGHT_GRAY)
                    pdf.set_font("Helvetica", "", 9)
                    pdf.set_text_color(*self.MID_GRAY)

                    # Header row
                    col_w = w / max(len(history), 1)
                    for i, _ in enumerate(history):
                        label = f"Wk {i + 1}"
                        pdf.cell(col_w, 7, label, border=1, fill=True, align="C")
                    pdf.ln()

                    # Value row
                    for rank in history:
                        color = self.GREEN_TEXT if rank <= 3 else self.RED_TEXT if rank >= 7 else self.DARK_GRAY
                        pdf.set_text_color(*color)
                        pdf.set_font("Helvetica", "B", 11)
                        pdf.cell(col_w, 9, f"#{rank}", border=1, align="C")
                    pdf.ln()

                    pdf.set_text_color(*self.DARK_GRAY)
                    pdf.set_font("Helvetica", "", 10)
                    pdf.ln(4)

                    trend_desc = {
                        "improving": "Your ranking is trending upward. Keep doing what you are doing.",
                        "declining": "Your ranking is in a sustained decline. Immediate action is needed to reverse this trend before it becomes the new normal.",
                        "volatile": "Your ranking is fluctuating significantly. This often indicates that Google is testing your listing against competitors. Consistent activity on your profile can stabilize this.",
                        "stable": "Your ranking has been relatively stable. Focus on incremental improvements to move up.",
                    }
                    pdf.multi_cell(w, 5.5, trend_desc.get(rt["direction"], ""), new_x="LMARGIN", new_y="NEXT")
                pdf.ln(6)

            # Competitor Spotlight
            if "competitor_spotlight" in insights:
                cs = insights["competitor_spotlight"]
                self._section_heading(pdf, w, "Competitor Intelligence")

                pdf.set_fill_color(*self.SECTION_BG)
                pdf.set_draw_color(*self.LIGHT_GRAY)

                # Competitor comparison table header
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_text_color(*self.WHITE)
                pdf.set_fill_color(*self.ACCENT)
                pdf.cell(70, 8, "  Metric", fill=True, border=1)
                pdf.cell(50, 8, "You", fill=True, border=1, align="C")
                pdf.cell(50, 8, _sanitize_for_pdf(cs["fastest_climber"][:20]), fill=True, border=1, align="C")
                pdf.ln()

                # Table rows
                rows = [
                    ("Current Rank", f"#{alert['curr_rank']}", f"#{cs['their_current_rank']}"),
                    ("Rating", f"{alert.get('rating', 'N/A')}", f"{cs['their_rating']}"),
                    ("Recent Review Gain", f"+{alert.get('reviews', 0) - alert.get('prev_reviews', 0)}",
                     f"+{cs['their_review_gain']}"),
                    ("Momentum", "Declining", f"Climbing (+{cs['climbed_positions']} pos)"),
                ]
                pdf.set_fill_color(*self.SECTION_BG)
                for label, you_val, comp_val in rows:
                    pdf.set_font("Helvetica", "", 9)
                    pdf.set_text_color(*self.DARK_GRAY)
                    pdf.cell(70, 7, f"  {label}", fill=True, border=1)
                    pdf.set_text_color(*self.BLACK)
                    pdf.cell(50, 7, you_val, fill=True, border=1, align="C")
                    pdf.set_text_color(*self.ACCENT)
                    pdf.cell(50, 7, comp_val, fill=True, border=1, align="C")
                    pdf.ln()

                pdf.ln(6)

            # Category Health Score
            if "category_health" in insights:
                ch = insights["category_health"]
                self._section_heading(pdf, w, "Market Position Score")

                score = ch["score"]
                color = self.GREEN_TEXT if score >= 7 else self.RED_TEXT if score < 5 else self.DARK_GRAY

                self._metric_card_row(pdf, w, [
                    ("Your Score", f"{score}/10", color),
                    ("Category Avg Reviews", str(ch["category_avg_reviews"]), self.DARK_GRAY),
                    ("Category Avg Rating", str(ch["category_avg_rating"]), self.DARK_GRAY),
                ])
                pdf.ln(3)
                pdf.set_font("Helvetica", "", 10)
                pdf.set_text_color(*self.DARK_GRAY)
                summary = ch["position_summary"]
                if summary == "needs attention":
                    pdf.multi_cell(w, 5.5,
                        "Your market position needs attention. You are below the category average "
                        "in key metrics. Focus on closing the review gap first -- it is the fastest "
                        "lever to pull.",
                        new_x="LMARGIN", new_y="NEXT")
                elif summary == "above average":
                    pdf.multi_cell(w, 5.5,
                        "You are performing above the category average. Your drop is likely due to "
                        "a specific competitor action rather than a systemic issue. The recommended "
                        "actions should be sufficient to recover.",
                        new_x="LMARGIN", new_y="NEXT")
                else:
                    pdf.multi_cell(w, 5.5,
                        "You are competitive but not dominant. Small improvements in reviews and "
                        "profile completeness can move you into the top tier.",
                        new_x="LMARGIN", new_y="NEXT")

        # Trend / Competitor sections from Claude text (if present)
        extra_sections = {k: v for k, v in sections.items()
                         if k not in ("what happened", "why", "quick wins")}
        if extra_sections and not has_insights:
            # Only render Claude text sections if we don't have structured insights
            pdf.add_page()
            self._page_header(pdf, w, "Additional Analysis", report_date)
            for title, lines in extra_sections.items():
                self._section_heading(pdf, w, title.title())
                pdf.set_font("Helvetica", "", 10)
                pdf.set_text_color(*self.DARK_GRAY)
                for line in lines:
                    if line.strip():
                        pdf.multi_cell(w, 5.5, line.strip(), new_x="LMARGIN", new_y="NEXT")
                pdf.ln(4)

        # ═══════════════════════════════════════════════════════════════════
        # FINAL PAGE — CTA / PRICING
        # ═══════════════════════════════════════════════════════════════════
        pdf.add_page()
        self._page_header(pdf, w, "Next Steps", report_date)

        pdf.ln(6)
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(*self.DARK_GRAY)
        pdf.multi_cell(w, 6,
            "This audit identified specific, actionable steps to recover your ranking. "
            "Local SEO is a continuous process -- businesses that monitor weekly and "
            "respond quickly to changes consistently outperform those that react only "
            "after significant drops.",
            new_x="LMARGIN", new_y="NEXT")

        pdf.ln(8)

        # Pricing cards
        self._section_heading(pdf, w, "Our Services")
        pdf.ln(2)

        # Service 1: One-time audit
        pdf.set_fill_color(*self.SECTION_BG)
        pdf.set_draw_color(*self.LIGHT_GRAY)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*self.BLACK)
        pdf.cell(w, 9, "  Deep-Dive SEO Audit Report", fill=True, border="LTR",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*self.DARK_GRAY)
        pdf.cell(w, 7, "  Complete competitive analysis with 10+ specific recommendations", fill=True, border="LR",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.cell(w, 7, "  Google Business Profile optimization checklist", fill=True, border="LR",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.cell(w, 7, "  Review strategy tailored to your market", fill=True, border="LR",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*self.ACCENT)
        pdf.cell(w, 10, "  $10 one-time", fill=True, border="LBR",
                 new_x="LMARGIN", new_y="NEXT")

        pdf.ln(6)

        # Service 2: Weekly monitoring
        pdf.set_fill_color(*self.WHITE)
        pdf.set_draw_color(*self.ACCENT)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*self.WHITE)
        pdf.set_fill_color(*self.ACCENT)
        pdf.cell(w, 9, "  Map Pack Guardian -- Weekly Monitoring", fill=True, border="LTR",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_fill_color(235, 245, 255)
        pdf.set_text_color(*self.DARK_GRAY)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(w, 7, "  Weekly rank tracking with instant drop alerts", fill=True, border="LR",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.cell(w, 7, "  Competitor movement intelligence", fill=True, border="LR",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.cell(w, 7, "  Review velocity monitoring", fill=True, border="LR",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.cell(w, 7, "  Monthly trend reports with actionable insights", fill=True, border="LR",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*self.ACCENT)
        pdf.cell(w, 10, "  $5/month", fill=True, border="LBR",
                 new_x="LMARGIN", new_y="NEXT")

        pdf.ln(10)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*self.MID_GRAY)
        pdf.multi_cell(w, 5.5,
            "Reply to this email to get started, or visit sutraflow.org to learn more. "
            "Cancel anytime -- no contracts, no commitments.",
            new_x="LMARGIN", new_y="NEXT")

        # Footer on same page
        pdf.ln(12)
        pdf.set_draw_color(*self.LIGHT_GRAY)
        pdf.line(20, pdf.get_y(), 190, pdf.get_y())
        pdf.ln(4)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*self.LIGHT_GRAY)
        pdf.cell(w, 4, "LocalRank Sentinel  |  sutraflow.org  |  Automated Local SEO Intelligence",
                 align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(w, 4, f"Report generated {report_date}. Data sourced from Google Maps.",
                 align="C")

        # Save PDF
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "" for c in alert["business_name"])
        safe_name = safe_name.replace(" ", "-").lower()[:50]
        date_str = datetime.now().strftime("%Y%m%d")
        filename = f"audit_{safe_name}_{date_str}.pdf"
        filepath = self.reports_dir / filename
        pdf.output(str(filepath))

        logger.info(f"Report Agent: generated {filepath}")
        return filepath

    # ── PDF helper methods ───────────────────────────────────────────────

    def _page_header(self, pdf: FPDF, w: float, title: str, date: str):
        """Render a consistent page header with line separator."""
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*self.BLACK)
        pdf.cell(w * 0.7, 8, title, new_x="RIGHT")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*self.MID_GRAY)
        pdf.cell(w * 0.3, 8, date, align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(*self.ACCENT)
        pdf.set_line_width(0.5)
        pdf.line(20, pdf.get_y(), 190, pdf.get_y())
        pdf.set_line_width(0.2)
        pdf.ln(6)

    def _section_heading(self, pdf: FPDF, w: float, title: str):
        """Render a section heading with left accent bar."""
        y = pdf.get_y()
        pdf.set_fill_color(*self.ACCENT)
        pdf.rect(20, y, 3, 7, "F")
        pdf.set_xy(26, y)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*self.BLACK)
        pdf.cell(w - 6, 7, title.upper(), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

    def _metric_card_row(self, pdf: FPDF, w: float, cards: list[tuple]):
        """Render a row of 3 metric cards. Each card: (label, value, color_tuple)."""
        card_w = w / 3 - 2
        start_x = pdf.l_margin
        y = pdf.get_y()

        for i, (label, value, color) in enumerate(cards):
            x = start_x + i * (card_w + 3)
            # Card background
            pdf.set_fill_color(*self.SECTION_BG)
            pdf.set_draw_color(*self.LIGHT_GRAY)
            pdf.rect(x, y, card_w, 22, "DF")
            # Label
            pdf.set_xy(x + 4, y + 3)
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*self.MID_GRAY)
            pdf.cell(card_w - 8, 4, label)
            # Value
            pdf.set_xy(x + 4, y + 10)
            pdf.set_font("Helvetica", "B", 14)
            pdf.set_text_color(*color)
            pdf.cell(card_w - 8, 8, value)

        pdf.set_y(y + 25)

    def _parse_audit_sections(self, audit_text: str) -> dict:
        """Parse Claude's audit text into named sections."""
        sections = {}
        current_key = None
        current_lines = []
        section_headers = ("what happened", "why", "quick wins", "your trend",
                           "competitor to watch", "your standing")

        for line in audit_text.split("\n"):
            stripped = line.strip()
            matched = False
            for header in section_headers:
                if stripped.upper().startswith(header.upper()):
                    if current_key is not None:
                        sections[current_key] = current_lines
                    current_key = header
                    # Don't include the header line itself as content
                    remainder = stripped[len(header):].strip().lstrip(":").lstrip("-").strip()
                    current_lines = [remainder] if remainder else []
                    matched = True
                    break
            if not matched and current_key is not None:
                current_lines.append(line)

        if current_key is not None:
            sections[current_key] = current_lines

        return sections

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
