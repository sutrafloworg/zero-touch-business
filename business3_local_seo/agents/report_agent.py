"""
Report Agent — generates professional Local SEO audit PDFs using Claude + matplotlib.

For each business with a rank drop, generates a multi-page PDF report:
  - Cover with key metrics
  - Executive summary with root cause analysis
  - Performance Intelligence with charts (ranking trend, review velocity, competitor comparison)
  - Market position analysis with visual gauge
  - Next steps with pricing

Uses fpdf2 for PDF layout, matplotlib for embedded charts.
"""
import io
import logging
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from fpdf import FPDF

logger = logging.getLogger(__name__)

AUDIT_PROMPT = """You are a local SEO analyst writing a specific, evidence-based audit for a business owner.
Your job is to verbalize the facts below into readable prose. Do NOT invent metrics, scores, or causes
that are not in the data. Every claim must trace back to a number in the input.

STRICT OUTPUT RULES:
- Never use: "exactly why", "the reason is", "this caused", "guaranteed", "certainly"
- Always use: "likely", "probable cause", "the data suggests", "our scan detected"
- If data is limited, say so — do not pad with generic SEO advice
- Output plain text only, no markdown, no asterisks, no dashes as bullets

== VERIFIED DATA (source: automated public data scan) ==
BUSINESS: {business_name}
CATEGORY: {category}
CITY: {city}
SCAN DATE: {scan_date}
PREVIOUS RANK: #{prev_rank}  |  CURRENT RANK: #{curr_rank}  |  DROP: {rank_change} positions
RATING: {rating} stars  |  TOTAL REVIEWS: {reviews}
WEEKS OF DATA: {weeks_tracked}
CONFIDENCE SCORE: {confidence_score}/10 (based on data completeness)

DETECTED SIGNALS (machine-computed facts only):
{reasons}
{insights_text}

== WRITE THE FOLLOWING SECTIONS (plain text, no markdown) ==

SECTION 1 — WHAT OUR SCAN FOUND (2-3 sentences)
State the rank drop as a fact. State 1-2 specific numbers from the data above that are notable.
Example: "Our scan on [date] recorded {business_name} at position #{curr_rank}, down from #{prev_rank} the prior week.
[Insert one specific competitor fact or review gap if data exists]."

SECTION 2 — PROBABLE CAUSES (2-3 bullet items, plain text dashes)
Label each: [HIGH CONFIDENCE], [MEDIUM CONFIDENCE], or [LOW CONFIDENCE] based on how directly the data supports it.
Only include causes that map to a detected signal above. Do not add generic SEO theory.
Format: "- [HIGH CONFIDENCE] Competitor X gained N reviews in N days while your profile gained 0."

SECTION 3 — PRIORITY ACTIONS (3 items max, only if data supports them)
For each action include: what to do, why (cite the specific data signal), estimated effort (Low/Med/High),
and expected impact (Low/Med/High).
Format: "Action: [specific action]. Why now: [cite the signal]. Effort: Low. Expected impact: High."
Be specific to this business type — a plumber gets different advice than a law firm.

{insights_section}

SECTION 4 — DO THIS TODAY (ready-to-use assets, only include if review gap detected)
If the review velocity data shows a gap, write:
a) 2 SMS review request templates personalized for a {category} in {city} (under 160 chars each)
b) 1 Google Business Profile post draft (2-3 sentences, action-oriented)
If no review gap data exists, omit this section entirely.

SECTION 5 — CONFIDENCE NOTE (1 sentence)
State the confidence score and what it means. If confidence < 7, flag that the drop may not be sustained
and recommend waiting for next week's scan before taking major action.

Tone: direct, like a trusted analyst — not a salesperson, not a cheerleader. Keep the whole response under 400 words."""


# Banned phrases that indicate overconfident AI generation — validated post-generation
BANNED_PHRASES = [
    "exactly why", "the reason is", "this caused", "guaranteed recovery",
    "will recover", "definitely", "certainly", "proven to", "always works",
    "the algorithm", "google decided", "google penalized",
]


def _validate_audit_text(text: str) -> tuple[bool, list[str]]:
    """Check for banned phrases and return (is_valid, list_of_violations)."""
    violations = []
    text_lower = text.lower()
    for phrase in BANNED_PHRASES:
        if phrase in text_lower:
            violations.append(phrase)
    return len(violations) == 0, violations


def _compute_confidence_score(alert: dict) -> int:
    """Compute 1-10 confidence score based on data completeness."""
    score = 3  # base: we have rank + business name
    insights = alert.get("insights", {})
    weeks = alert.get("weeks_tracked", 1)

    if weeks >= 3:
        score += 1
    if weeks >= 5:
        score += 1
    if "review_velocity" in insights:
        score += 1
    if "rank_trend" in insights:
        score += 1
    if "competitor_spotlight" in insights:
        score += 1
    if "category_health" in insights:
        score += 1
    if alert.get("reasons"):
        score += 1

    return min(score, 10)


def _format_insights_for_prompt(alert: dict) -> tuple[str, str]:
    """Format progressive insights into text for the Claude prompt."""
    insights = alert.get("insights", {})
    text_parts = []
    section_parts = []

    if "review_velocity" in insights:
        rv = insights["review_velocity"]
        text_parts.append(
            f"REVIEW VELOCITY (measured): {rv['reviews_per_week']} reviews/week over {rv['over_weeks']} weeks "
            f"({rv['verdict']}). Total gained in period: {rv['total_gained']}."
        )

    if "rank_trend" in insights:
        rt = insights["rank_trend"]
        text_parts.append(
            f"RANK TREND (measured): {rt['direction']} over last {len(rt['history'])} weeks. "
            f"Best: #{rt['best_rank']}, Worst: #{rt['worst_rank']}. "
            f"Weekly positions: {', '.join(f'#{r}' for r in rt['history'])}"
        )
        section_parts.append(
            "Include in PROBABLE CAUSES: one bullet about the multi-week trend direction "
            "based on rank history data (improving/declining/volatile)."
        )

    if "competitor_spotlight" in insights:
        cs = insights["competitor_spotlight"]
        text_parts.append(
            f"COMPETITOR DATA (measured): {cs['fastest_climber']} moved from "
            f"#{cs.get('their_prev_rank', '?')} to #{cs['their_current_rank']} "
            f"(+{cs['climbed_positions']} positions), gained {cs['their_review_gain']} reviews "
            f"in the scan period, rated {cs['their_rating']} stars."
        )
        section_parts.append(
            "Include in PROBABLE CAUSES: one bullet naming the specific competitor and their "
            "exact review gain, marked [HIGH CONFIDENCE] if review gain > 5, else [MEDIUM CONFIDENCE]."
        )

    if "category_health" in insights:
        ch = insights["category_health"]
        text_parts.append(
            f"MARKET POSITION (measured): Category health score {ch['score']}/10 ({ch['position_summary']}). "
            f"Your reviews: {ch['your_reviews']} vs market avg: {ch['category_avg_reviews']}. "
            f"Your rating: {ch['your_rating']} vs market avg: {ch['category_avg_rating']}."
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
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _format_city(raw_city: str) -> str:
    """Convert 'newyork' → 'New York', 'losangeles' → 'Los Angeles'."""
    known = {
        "newyork": "New York", "losangeles": "Los Angeles",
        "sanfrancisco": "San Francisco", "sandiego": "San Diego",
        "sanjose": "San Jose", "lasvegas": "Las Vegas",
        "fortworth": "Fort Worth", "sanantonio": "San Antonio",
    }
    lower = raw_city.lower().replace(" ", "")
    return known.get(lower, raw_city.replace("-", " ").title())


def _format_category(raw_cat: str) -> str:
    """Convert 'personal-injury-lawyer' → 'Personal Injury Lawyer'."""
    return raw_cat.replace("-", " ").title()


# ── Chart Generation (matplotlib) ────────────────────────────────────────────

def _create_ranking_trend_chart(history: list[int], direction: str) -> str:
    """Create a line chart of ranking history. Returns path to temp PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker

    fig, ax = plt.subplots(figsize=(6.5, 2.2), dpi=150)

    weeks = list(range(1, len(history) + 1))
    colors = {"declining": "#b41e1e", "volatile": "#d97706", "improving": "#1e8232", "stable": "#0066cc"}
    line_color = colors.get(direction, "#0066cc")

    # Invert y-axis (rank 1 at top)
    ax.invert_yaxis()

    # Fill under the line
    ax.fill_between(weeks, history, max(history) + 1, alpha=0.08, color=line_color)

    # Plot line with markers
    ax.plot(weeks, history, color=line_color, linewidth=2.5, marker="o",
            markersize=8, markerfacecolor="white", markeredgewidth=2.5,
            markeredgecolor=line_color, zorder=5)

    # Annotate each point
    for w, r in zip(weeks, history):
        color = "#1e8232" if r <= 3 else "#b41e1e" if r >= 7 else "#333333"
        ax.annotate(f"#{r}", (w, r), textcoords="offset points", xytext=(0, -18),
                    ha="center", fontsize=9, fontweight="bold", color=color)

    # Top 3 zone
    ax.axhspan(0.5, 3.5, alpha=0.06, color="#1e8232", zorder=0)
    ax.text(len(weeks) + 0.3, 2, "Top 3\n(visible)", fontsize=7, color="#1e8232",
            ha="left", va="center", style="italic")

    ax.set_xlabel("Week", fontsize=9, color="#666666")
    ax.set_ylabel("Rank Position", fontsize=9, color="#666666")
    ax.set_xticks(weeks)
    ax.set_xticklabels([f"Wk {w}" for w in weeks], fontsize=8)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.set_xlim(0.5, len(weeks) + 0.8)
    ax.set_ylim(max(history) + 1, 0.5)
    ax.tick_params(axis="both", labelsize=8, colors="#999999")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#dddddd")
    ax.spines["bottom"].set_color("#dddddd")
    ax.grid(axis="y", alpha=0.15, linestyle="--")

    fig.tight_layout(pad=0.5)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    fig.savefig(tmp.name, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return tmp.name


def _create_review_velocity_chart(your_velocity: float, your_reviews: int,
                                   cat_avg_reviews: int) -> str:
    """Create a horizontal bar chart comparing review metrics. Returns path to PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.5, 1.6), dpi=150,
                                     gridspec_kw={"width_ratios": [1, 1.2]})

    # Left: velocity gauge
    target = 3.0  # industry benchmark
    bar_width = 0.4
    ax1.barh(0, your_velocity, height=bar_width, color="#b41e1e" if your_velocity < 1.5 else "#d97706" if your_velocity < 3 else "#1e8232",
             zorder=3, label=f"You: {your_velocity}/wk")
    ax1.axvline(x=target, color="#0066cc", linestyle="--", linewidth=1.5, zorder=4)
    ax1.text(target + 0.1, 0.3, f"Target: {target}/wk", fontsize=7, color="#0066cc", va="bottom")
    ax1.set_xlim(0, max(target * 1.5, your_velocity * 1.3))
    ax1.set_yticks([])
    ax1.set_xlabel("Reviews per Week", fontsize=8, color="#666666")
    ax1.set_title("Your Review Velocity", fontsize=9, fontweight="bold", color="#333333", pad=8)
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)
    ax1.spines["left"].set_visible(False)
    ax1.spines["bottom"].set_color("#dddddd")
    ax1.tick_params(axis="x", labelsize=7, colors="#999999")

    # Right: total reviews comparison
    labels = ["You", "Category Avg"]
    values = [your_reviews, cat_avg_reviews]
    colors = ["#b41e1e" if your_reviews < cat_avg_reviews * 0.7 else "#d97706" if your_reviews < cat_avg_reviews else "#1e8232",
              "#0066cc"]
    bars = ax2.barh(labels, values, height=0.5, color=colors, zorder=3)
    for bar, val in zip(bars, values):
        ax2.text(val + max(values) * 0.02, bar.get_y() + bar.get_height() / 2,
                 str(val), va="center", fontsize=9, fontweight="bold", color="#333333")
    ax2.set_xlim(0, max(values) * 1.2)
    ax2.set_title("Total Reviews vs Market", fontsize=9, fontweight="bold", color="#333333", pad=8)
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)
    ax2.spines["left"].set_color("#dddddd")
    ax2.spines["bottom"].set_color("#dddddd")
    ax2.tick_params(axis="both", labelsize=8, colors="#999999")
    ax2.invert_yaxis()

    fig.tight_layout(pad=1.0)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    fig.savefig(tmp.name, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return tmp.name


def _create_health_gauge(score: int) -> str:
    """Create a semicircular gauge for health score. Returns path to PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fig, ax = plt.subplots(figsize=(2.8, 1.6), dpi=150, subplot_kw={"projection": "polar"})

    # Gauge from 180 to 0 degrees (left to right)
    theta_bg = np.linspace(np.pi, 0, 100)
    ax.fill_between(theta_bg, 0.6, 1.0, alpha=0.08, color="#999999")

    # Color segments
    segments = [
        (np.linspace(np.pi, np.pi * 0.7, 30), "#b41e1e"),      # 0-3: red
        (np.linspace(np.pi * 0.7, np.pi * 0.5, 20), "#d97706"),  # 3-5: orange
        (np.linspace(np.pi * 0.5, np.pi * 0.3, 20), "#d97706"),  # 5-7: orange
        (np.linspace(np.pi * 0.3, 0, 30), "#1e8232"),            # 7-10: green
    ]
    for seg_theta, seg_color in segments:
        ax.plot(seg_theta, [0.95] * len(seg_theta), color=seg_color, linewidth=8, alpha=0.3)

    # Needle
    needle_angle = np.pi * (1 - score / 10)
    ax.annotate("", xy=(needle_angle, 0.85), xytext=(needle_angle, 0.2),
                arrowprops=dict(arrowstyle="->, head_width=0.15", color="#0f0f0f", lw=2))

    # Score text
    color = "#b41e1e" if score < 4 else "#d97706" if score < 7 else "#1e8232"
    ax.text(np.pi / 2, 0.15, f"{score}/10", ha="center", va="center",
            fontsize=22, fontweight="bold", color=color,
            transform=ax.transData)

    # Labels
    ax.text(np.pi, 0.55, "0", ha="center", fontsize=7, color="#999999")
    ax.text(0, 0.55, "10", ha="center", fontsize=7, color="#999999")

    ax.set_ylim(0, 1.05)
    ax.set_thetamin(0)
    ax.set_thetamax(180)
    ax.axis("off")

    fig.tight_layout(pad=0)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    fig.savefig(tmp.name, bbox_inches="tight", facecolor="white", transparent=True)
    plt.close(fig)
    return tmp.name


def _create_competitor_chart(your_rank: int, your_rating: float, your_reviews: int,
                              comp_name: str, comp_rank: int, comp_rating: float,
                              comp_reviews: int) -> str:
    """Create a grouped bar chart comparing you vs top competitor. Returns path to PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fig, axes = plt.subplots(1, 3, figsize=(6.5, 1.8), dpi=150)

    categories = ["Rank", "Rating", "Recent Reviews"]
    your_vals = [your_rank, your_rating, your_reviews]
    comp_vals = [comp_rank, comp_rating, comp_reviews]

    # For recent reviews, show review gain not total
    # (comp_reviews is already their_review_gain from caller)

    for i, (ax, cat, yv, cv) in enumerate(zip(axes, categories, your_vals, comp_vals)):
        x = np.array([0, 0.6])
        bars = ax.bar(x, [yv, cv], width=0.45,
                      color=["#333333", "#0066cc"], zorder=3)

        # Value labels
        for bar, val in zip(bars, [yv, cv]):
            label = f"#{val}" if cat == "Rank" else str(val)
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(yv, cv) * 0.05,
                    label, ha="center", va="bottom", fontsize=9, fontweight="bold", color="#333333")

        ax.set_xticks(x)
        ax.set_xticklabels(["You", _sanitize_for_pdf(comp_name[:12])], fontsize=7, color="#666666")
        ax.set_title(cat, fontsize=9, fontweight="bold", color="#333333", pad=6)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#dddddd")
        ax.spines["bottom"].set_color("#dddddd")
        ax.tick_params(axis="y", labelsize=7, colors="#999999")

        # For rank, invert (lower is better)
        if cat == "Rank":
            ax.set_ylim(0, max(yv, cv) * 1.4)
            # Add "better" arrow
            ax.annotate("", xy=(0.3, max(yv, cv) * 1.3), xytext=(0.3, max(yv, cv) * 0.9),
                        arrowprops=dict(arrowstyle="->", color="#1e8232", lw=1))
            ax.text(0.3, max(yv, cv) * 1.35, "lower\nis better", fontsize=5, ha="center",
                    color="#1e8232", style="italic")

    fig.tight_layout(pad=1.0)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    fig.savefig(tmp.name, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return tmp.name


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
        """Generate a PDF audit report for a single rank-drop alert."""
        parts = alert["category_key"].split("_")
        city_raw = parts[0] if parts else "Unknown"
        state_raw = parts[1].upper() if len(parts) > 1 else ""
        category_raw = parts[2] if len(parts) > 2 else "Business"

        city = _format_city(city_raw)
        category = _format_category(category_raw)

        scan_date = datetime.now(timezone.utc).strftime("%B %d, %Y")
        confidence_score = _compute_confidence_score(alert)
        reasons_text = "\n".join(f"- {r}" for r in alert.get("reasons", []))
        insights_text, insights_section = _format_insights_for_prompt(alert)

        prompt = AUDIT_PROMPT.format(
            business_name=alert["business_name"],
            category=category,
            city=f"{city}, {state_raw}",
            scan_date=scan_date,
            prev_rank=alert["prev_rank"],
            curr_rank=alert["curr_rank"],
            rank_change=alert["rank_change"],
            rating=alert.get("rating", "N/A"),
            reviews=alert.get("reviews", 0),
            weeks_tracked=alert.get("weeks_tracked", 1),
            confidence_score=confidence_score,
            reasons=reasons_text,
            insights_text=insights_text,
            insights_section=insights_section,
        )

        try:
            audit_text = self._call_claude(prompt)
            # Validate output for banned phrases
            is_valid, violations = _validate_audit_text(audit_text)
            if not is_valid:
                logger.warning(
                    f"Report Agent: banned phrases detected for {alert['business_name']}: {violations}. "
                    "Replacing with confidence-hedged language."
                )
                for phrase in violations:
                    audit_text = audit_text.replace(phrase, "likely " + phrase.split()[-1] if phrase.split() else phrase)
            audit_text = _sanitize_for_pdf(audit_text)
        except Exception as e:
            logger.error(f"Report Agent: Claude failed for {alert['business_name']}: {e}")
            return None

        # Store confidence on alert for PDF rendering
        alert["_confidence_score"] = confidence_score
        alert["_scan_date"] = scan_date
        return self._build_pdf(alert, audit_text, city, state_raw, category)

    # ── PDF color constants ────────────────────────────────────────────────
    BLACK = (15, 15, 15)
    WHITE = (255, 255, 255)
    DARK_GRAY = (50, 50, 50)
    MID_GRAY = (100, 100, 100)
    LIGHT_GRAY = (200, 200, 200)
    ACCENT = (0, 102, 204)
    RED_TEXT = (180, 30, 30)
    RED_BG = (255, 243, 243)
    GREEN_TEXT = (30, 130, 50)
    GREEN_BG = (240, 255, 244)
    SECTION_BG = (247, 248, 250)

    def _build_pdf(
        self,
        alert: dict,
        audit_text: str,
        city: str,
        state: str,
        category: str,
    ) -> Path:
        """Build a professional multi-page PDF audit report with charts."""
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

        # Generate chart images
        chart_files = []
        try:
            chart_images = self._generate_charts(alert, insights)
            chart_files = list(chart_images.values())
        except Exception as e:
            logger.warning(f"Chart generation failed, proceeding without charts: {e}")
            chart_images = {}

        pdf = FPDF()
        pdf.set_left_margin(20)
        pdf.set_right_margin(20)
        pdf.set_auto_page_break(auto=True, margin=25)
        w = 170

        # ═══ PAGE 1 — COVER ═══════════════════════════════════════════════
        pdf.add_page()

        # Full-width dark header
        pdf.set_fill_color(*self.BLACK)
        pdf.rect(0, 0, 210, 80, "F")
        pdf.set_fill_color(*self.ACCENT)
        pdf.rect(0, 80, 210, 3, "F")

        # Brand
        pdf.set_text_color(*self.WHITE)
        pdf.set_font("Helvetica", "B", 28)
        pdf.set_xy(20, 18)
        pdf.cell(w, 12, "Search Sentinel", new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", "", 12)
        pdf.set_xy(20, 35)
        pdf.set_text_color(180, 190, 200)
        pdf.cell(w, 7, "Local SEO Intelligence & Monitoring", new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", "B", 14)
        pdf.set_xy(20, 52)
        pdf.set_text_color(*self.WHITE)
        pdf.cell(w, 8, "Ranking Audit Report", new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", "", 10)
        pdf.set_xy(20, 64)
        pdf.set_text_color(160, 170, 180)
        pdf.cell(w, 6, report_date, new_x="LMARGIN", new_y="NEXT")

        # Business details
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
        pdf.cell(w, 7, f"{category}  |  {city}, {state}", new_x="LMARGIN", new_y="NEXT")

        # Key metrics
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

        # Status bar
        pdf.ln(12)
        if weeks > 1 and insights:
            badge_parts = [f"Week {weeks} of monitoring"]
            if "review_velocity" in insights:
                badge_parts.append(f"{insights['review_velocity']['reviews_per_week']} reviews/wk")
            if "rank_trend" in insights:
                badge_parts.append(f"Trend: {insights['rank_trend']['direction']}")
            if "category_health" in insights:
                badge_parts.append(f"Health: {insights['category_health']['score']}/10")
            pdf.set_fill_color(*self.SECTION_BG)
            pdf.set_draw_color(*self.LIGHT_GRAY)
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(*self.MID_GRAY)
            pdf.cell(w, 10, "   ".join(badge_parts), border=1, fill=True, align="C",
                     new_x="LMARGIN", new_y="NEXT")

        # Confidentiality + data source
        pdf.set_y(-45)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(*self.LIGHT_GRAY)
        pdf.cell(w, 5, "CONFIDENTIAL -- Prepared exclusively for the business named above.",
                 align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(w, 5, "sutraflow.org", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 7)
        pdf.cell(w, 4, "Data source: Google Maps Local Pack results. Collected via automated weekly scans.",
                 align="C")

        # ═══ PAGE 2 — EXECUTIVE SUMMARY ═══════════════════════════════════
        pdf.add_page()
        self._page_header(pdf, w, "Executive Summary", report_date)

        sections = self._parse_audit_sections(audit_text)

        # What Happened
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

        # Impact callout
        rank = alert["curr_rank"]
        if rank > 3:
            pdf.set_fill_color(255, 251, 235)
            pdf.set_draw_color(217, 119, 6)
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(146, 64, 14)
            pdf.cell(w, 7, "  BUSINESS IMPACT", fill=True, border="LTR",
                     new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(w, 6, f"  Positions 4+ are below the fold on mobile. Google data shows 90% of",
                     fill=True, border="LR", new_x="LMARGIN", new_y="NEXT")
            pdf.cell(w, 6, f"  clicks go to the top 3 results. At position #{rank}, you are losing",
                     fill=True, border="LR", new_x="LMARGIN", new_y="NEXT")
            pct_lost = min(95, 60 + (rank - 3) * 8)
            pdf.cell(w, 6, f"  an estimated {pct_lost}% of potential leads from Google Maps searches.",
                     fill=True, border="LBR", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)

        # Why
        if "why" in sections:
            self._section_heading(pdf, w, "Root Cause Analysis")
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(*self.DARK_GRAY)
            for line in sections["why"]:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("-"):
                    pdf.set_x(25)
                    pdf.multi_cell(w - 5, 5.5, line, new_x="LMARGIN", new_y="NEXT")
                else:
                    pdf.multi_cell(w, 5.5, line, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)

        # Quick Wins
        if "quick wins" in sections:
            self._section_heading(pdf, w, "Recommended Actions")
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

        # Data source footnote
        pdf.set_font("Helvetica", "I", 7)
        pdf.set_text_color(*self.LIGHT_GRAY)
        pdf.cell(w, 4, "Analysis based on Google Maps Local Pack data. Rankings may vary by device and location.",
                 new_x="LMARGIN", new_y="NEXT")

        # ═══ PAGE 3 — PERFORMANCE INTELLIGENCE (with charts) ═══════════
        has_insights = any(k in insights for k in
                          ("review_velocity", "rank_trend", "competitor_spotlight", "category_health"))

        if has_insights or weeks > 1:
            pdf.add_page()
            self._page_header(pdf, w, "Performance Intelligence", report_date)

            # Ranking Trend Chart
            if "rank_trend" in chart_images:
                self._section_heading(pdf, w, "Ranking Trend")
                rt = insights.get("rank_trend", {})
                pdf.image(chart_images["rank_trend"], x=20, y=pdf.get_y(), w=w)
                pdf.ln(52)

                trend_desc = {
                    "improving": "Your ranking is trending upward. Keep doing what you are doing -- consistency is key.",
                    "declining": "Your ranking is in a sustained decline. Immediate action is needed to reverse this trend before it becomes the new normal.",
                    "volatile": "Your ranking is fluctuating significantly. This often indicates that Google is testing your listing against competitors. Consistent activity on your profile can stabilize this.",
                    "stable": "Your ranking has been relatively stable. Focus on incremental improvements to move up.",
                }
                pdf.set_font("Helvetica", "", 10)
                pdf.set_text_color(*self.DARK_GRAY)
                pdf.multi_cell(w, 5.5, trend_desc.get(rt.get("direction", ""), ""),
                               new_x="LMARGIN", new_y="NEXT")
                pdf.ln(6)

            # Review Velocity Chart
            if "review_velocity" in chart_images:
                self._section_heading(pdf, w, "Review Performance")
                pdf.image(chart_images["review_velocity"], x=20, y=pdf.get_y(), w=w)
                pdf.ln(40)

                rv = insights.get("review_velocity", {})
                if rv.get("verdict") == "stagnant":
                    pdf.set_font("Helvetica", "", 10)
                    pdf.set_text_color(*self.DARK_GRAY)
                    pdf.multi_cell(w, 5.5,
                        "Your review growth has stalled. According to BrightLocal's 2025 Local Consumer "
                        "Review Survey, 87% of consumers read online reviews before visiting a local business. "
                        "Businesses averaging 3+ reviews/week consistently outrank those below 1/week.",
                        new_x="LMARGIN", new_y="NEXT")
                elif rv.get("verdict") == "strong":
                    pdf.set_font("Helvetica", "", 10)
                    pdf.set_text_color(*self.DARK_GRAY)
                    pdf.multi_cell(w, 5.5,
                        "Strong review velocity. You are building social proof faster than most "
                        "competitors. Google's local ranking algorithm weighs review recency heavily "
                        "-- maintain this pace to protect your position.",
                        new_x="LMARGIN", new_y="NEXT")
                pdf.ln(4)

            # Data source
            pdf.set_font("Helvetica", "I", 7)
            pdf.set_text_color(*self.LIGHT_GRAY)
            pdf.cell(w, 4, "Sources: Google Maps API data collected weekly. Industry benchmarks from BrightLocal 2025 Local SEO Report.",
                     new_x="LMARGIN", new_y="NEXT")

        # ═══ PAGE 4 — COMPETITIVE ANALYSIS (if insights available) ═════
        has_competitor = "competitor_spotlight" in insights or "category_health" in insights
        if has_competitor:
            pdf.add_page()
            self._page_header(pdf, w, "Competitive Analysis", report_date)

            # Competitor Chart
            if "competitor" in chart_images:
                cs = insights.get("competitor_spotlight", {})
                self._section_heading(pdf, w, f"You vs {_sanitize_for_pdf(cs.get('fastest_climber', 'Top Competitor'))}")
                pdf.image(chart_images["competitor"], x=20, y=pdf.get_y(), w=w)
                pdf.ln(42)

                # Competitor table (full width, no truncation)
                pdf.set_fill_color(*self.ACCENT)
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_text_color(*self.WHITE)
                comp_name = _sanitize_for_pdf(cs.get("fastest_climber", "Competitor"))
                pdf.cell(60, 8, "  Metric", fill=True, border=1)
                pdf.cell(55, 8, "You", fill=True, border=1, align="C")
                pdf.cell(55, 8, comp_name, fill=True, border=1, align="C")
                pdf.ln()

                rows = [
                    ("Current Rank", f"#{alert['curr_rank']}", f"#{cs.get('their_current_rank', '?')}"),
                    ("Rating", f"{alert.get('rating', 'N/A')}", f"{cs.get('their_rating', '?')}"),
                    ("Recent Review Gain", f"+{alert.get('reviews', 0) - alert.get('prev_reviews', 0)}",
                     f"+{cs.get('their_review_gain', '?')}"),
                    ("Momentum", "Declining", f"Climbing (+{cs.get('climbed_positions', '?')} positions)"),
                ]
                pdf.set_fill_color(*self.SECTION_BG)
                for label, you_val, comp_val in rows:
                    pdf.set_font("Helvetica", "", 9)
                    pdf.set_text_color(*self.DARK_GRAY)
                    pdf.cell(60, 7, f"  {label}", fill=True, border=1)
                    pdf.set_text_color(*self.BLACK)
                    pdf.cell(55, 7, you_val, fill=True, border=1, align="C")
                    pdf.set_text_color(*self.ACCENT)
                    pdf.cell(55, 7, comp_val, fill=True, border=1, align="C")
                    pdf.ln()
                pdf.ln(6)

            # Health Score with gauge
            if "category_health" in insights:
                ch = insights["category_health"]
                self._section_heading(pdf, w, "Market Position Score")

                if "health_gauge" in chart_images:
                    # Gauge on left, metrics on right
                    gauge_y = pdf.get_y()
                    pdf.image(chart_images["health_gauge"], x=22, y=gauge_y, w=50)

                    # Metrics to the right of gauge
                    pdf.set_xy(80, gauge_y + 2)
                    pdf.set_font("Helvetica", "", 9)
                    pdf.set_text_color(*self.DARK_GRAY)

                    metrics = [
                        f"Your reviews: {ch['your_reviews']}  |  Category avg: {ch['category_avg_reviews']}",
                        f"Your rating: {ch['your_rating']}  |  Category avg: {ch['category_avg_rating']}",
                        f"Position: {ch['position_summary'].title()}",
                    ]
                    for i, m in enumerate(metrics):
                        pdf.set_xy(80, gauge_y + 6 + i * 7)
                        pdf.cell(90, 6, m)

                    pdf.set_y(gauge_y + 34)

                pdf.set_font("Helvetica", "", 10)
                pdf.set_text_color(*self.DARK_GRAY)
                summary = ch["position_summary"]
                if summary == "needs attention":
                    pdf.multi_cell(w, 5.5,
                        "Your market position needs attention. You are below the category average "
                        "in key metrics. According to Moz's Local Search Ranking Factors study, "
                        "review signals (quantity, velocity, diversity) account for approximately "
                        "17% of local pack ranking factors. Focus on closing the review gap first.",
                        new_x="LMARGIN", new_y="NEXT")
                elif summary == "above average":
                    pdf.multi_cell(w, 5.5,
                        "You are performing above the category average. Your drop is likely due to "
                        "a specific competitor action rather than a systemic issue. The recommended "
                        "actions should be sufficient to recover.",
                        new_x="LMARGIN", new_y="NEXT")
                else:
                    pdf.multi_cell(w, 5.5,
                        "You are competitive but not dominant. Whitespark's 2025 Local Ranking "
                        "Factors survey shows that Google Business Profile signals and review "
                        "signals together account for ~49% of local pack rankings. Small "
                        "improvements in both areas can move you into the top tier.",
                        new_x="LMARGIN", new_y="NEXT")

            # Data source
            pdf.ln(6)
            pdf.set_font("Helvetica", "I", 7)
            pdf.set_text_color(*self.LIGHT_GRAY)
            pdf.cell(w, 4, "Sources: Google Maps API, Moz Local Search Ranking Factors 2025, Whitespark Local Ranking Factors Survey 2025.",
                     new_x="LMARGIN", new_y="NEXT")

        # Extra Claude sections
        extra_sections = {k: v for k, v in sections.items()
                         if k not in ("what happened", "why", "quick wins")}
        if extra_sections and not has_insights:
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

        # ═══ FINAL PAGE — CTA / PRICING ═══════════════════════════════════
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
        self._section_heading(pdf, w, "Our Services")
        pdf.ln(2)

        # Service 1
        pdf.set_fill_color(*self.SECTION_BG)
        pdf.set_draw_color(*self.LIGHT_GRAY)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*self.BLACK)
        pdf.cell(w, 9, "  Deep-Dive SEO Audit Report", fill=True, border="LTR",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*self.DARK_GRAY)
        for item in ["Complete competitive analysis with 10+ specific recommendations",
                      "Google Business Profile optimization checklist",
                      "Review strategy tailored to your market",
                      "Keyword gap analysis vs top 3 competitors"]:
            pdf.cell(w, 7, f"  {item}", fill=True, border="LR",
                     new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*self.ACCENT)
        pdf.cell(w, 10, "  $10 one-time", fill=True, border="LBR",
                 new_x="LMARGIN", new_y="NEXT")

        pdf.ln(6)

        # Service 2
        pdf.set_fill_color(*self.ACCENT)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*self.WHITE)
        pdf.cell(w, 9, "  Map Pack Guardian -- Weekly Monitoring", fill=True, border="LTR",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_fill_color(235, 245, 255)
        pdf.set_text_color(*self.DARK_GRAY)
        pdf.set_font("Helvetica", "", 10)
        for item in ["Weekly rank tracking with instant drop alerts",
                      "Competitor movement intelligence",
                      "Review velocity monitoring",
                      "Monthly trend reports with visual analytics"]:
            pdf.cell(w, 7, f"  {item}", fill=True, border="LR",
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

        # Footer
        pdf.ln(12)
        pdf.set_draw_color(*self.LIGHT_GRAY)
        pdf.line(20, pdf.get_y(), 190, pdf.get_y())
        pdf.ln(4)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*self.LIGHT_GRAY)
        pdf.cell(w, 4, "Search Sentinel  |  sutraflow.org  |  Automated Local SEO Intelligence",
                 align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(w, 4, f"Report generated {report_date}. Data sourced from Google Maps.",
                 align="C")

        # Save
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "" for c in alert["business_name"])
        safe_name = safe_name.replace(" ", "-").lower()[:50]
        date_str = datetime.now().strftime("%Y%m%d")
        filename = f"audit_{safe_name}_{date_str}.pdf"
        filepath = self.reports_dir / filename
        pdf.output(str(filepath))

        # Clean up chart temp files
        for f in chart_files:
            try:
                Path(f).unlink(missing_ok=True)
            except Exception:
                pass

        logger.info(f"Report Agent: generated {filepath}")
        return filepath

    def _generate_charts(self, alert: dict, insights: dict) -> dict:
        """Generate all chart images. Returns {name: filepath} dict."""
        charts = {}

        if "rank_trend" in insights:
            rt = insights["rank_trend"]
            charts["rank_trend"] = _create_ranking_trend_chart(
                rt["history"], rt["direction"]
            )

        if "review_velocity" in insights and "category_health" in insights:
            rv = insights["review_velocity"]
            ch = insights["category_health"]
            charts["review_velocity"] = _create_review_velocity_chart(
                rv["reviews_per_week"], ch["your_reviews"], ch["category_avg_reviews"]
            )

        if "competitor_spotlight" in insights:
            cs = insights["competitor_spotlight"]
            your_review_gain = alert.get("reviews", 0) - alert.get("prev_reviews", 0)
            charts["competitor"] = _create_competitor_chart(
                alert["curr_rank"], alert.get("rating", 0), max(your_review_gain, 0),
                cs["fastest_climber"], cs["their_current_rank"],
                cs["their_rating"], cs.get("their_review_gain", 0),
            )

        if "category_health" in insights:
            charts["health_gauge"] = _create_health_gauge(
                insights["category_health"]["score"]
            )

        return charts

    # ── PDF helper methods ───────────────────────────────────────────────

    def _page_header(self, pdf: FPDF, w: float, title: str, date: str):
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
        y = pdf.get_y()
        pdf.set_fill_color(*self.ACCENT)
        pdf.rect(20, y, 3, 7, "F")
        pdf.set_xy(26, y)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*self.BLACK)
        pdf.cell(w - 6, 7, title.upper(), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

    def _metric_card_row(self, pdf: FPDF, w: float, cards: list[tuple]):
        card_w = w / 3 - 2
        start_x = pdf.l_margin
        y = pdf.get_y()

        for i, (label, value, color) in enumerate(cards):
            x = start_x + i * (card_w + 3)
            pdf.set_fill_color(*self.SECTION_BG)
            pdf.set_draw_color(*self.LIGHT_GRAY)
            pdf.rect(x, y, card_w, 22, "DF")
            pdf.set_xy(x + 4, y + 3)
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*self.MID_GRAY)
            pdf.cell(card_w - 8, 4, label)
            pdf.set_xy(x + 4, y + 10)
            pdf.set_font("Helvetica", "B", 14)
            pdf.set_text_color(*color)
            pdf.cell(card_w - 8, 8, value)

        pdf.set_y(y + 25)

    def _parse_audit_sections(self, audit_text: str) -> dict:
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
        """Generate PDFs for all alerts. Returns list of {alert, pdf_path} dicts."""
        results = []
        for alert in alerts:
            pdf_path = self.generate_audit(alert)
            if pdf_path:
                results.append({"alert": alert, "pdf_path": pdf_path})
            time.sleep(1)

        logger.info(f"Report Agent: generated {len(results)}/{len(alerts)} audit PDFs")
        return results
