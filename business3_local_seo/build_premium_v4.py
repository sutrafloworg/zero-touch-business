"""
Premium PDF Report Generator v4 — Search Sentinel
Generates a state-of-the-art sample audit PDF with:
  - Full-bleed colored backgrounds per section
  - Edge-to-edge visual density (no blank space)
  - Premium sidebar accent strips and gradient-like blocks
  - Rich typography hierarchy
  - Embedded charts (ranking trend, review velocity, competitor, health gauge)
  - Attribute Parity table
  - GBP Checklist
  - CTA/Pricing page

Usage: python build_premium_v4.py
Output: sample_audit_report_v4.pdf on Desktop
"""
import tempfile
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

# ── Color Palette ─────────────────────────────────────────────────────────────
NAVY       = (10, 25, 47)
DARK_NAVY  = (5, 15, 30)
WHITE      = (255, 255, 255)
OFF_WHITE  = (248, 249, 252)
LIGHT_GRAY = (220, 225, 232)
MID_GRAY   = (120, 130, 145)
DARK_GRAY  = (55, 60, 70)
ACCENT     = (0, 122, 255)
ACCENT_DK  = (0, 90, 200)
RED        = (220, 53, 69)
RED_BG     = (255, 240, 240)
GREEN      = (34, 139, 34)
GREEN_BG   = (235, 255, 240)
AMBER      = (255, 165, 0)
AMBER_BG   = (255, 248, 230)
GOLD       = (218, 165, 32)
SECTION_BG = (240, 243, 248)

# ── Sample Data ───────────────────────────────────────────────────────────────
ALERT = {
    "business_name": "Cellino Law",
    "category_key": "new york_ny_personal-injury-lawyer",
    "prev_rank": 3, "curr_rank": 7, "rank_change": 4,
    "rating": 4.2, "reviews": 89, "prev_reviews": 85,
    "weeks_tracked": 6,
    "reasons": [
        "Competitors above you gained reviews: Morgan & Morgan gained 12 new reviews",
        "No new reviews this week -- Google favors actively reviewed businesses",
        "3 competitor(s) above you have higher ratings",
    ],
    "insights": {
        "review_velocity": {"reviews_per_week": 0.8, "over_weeks": 6, "total_gained": 4, "verdict": "stagnant"},
        "rank_trend": {"direction": "declining", "history": [3, 3, 4, 5, 6, 7], "best_rank": 3, "worst_rank": 7},
        "competitor_spotlight": {
            "fastest_climber": "Morgan & Morgan", "climbed_positions": 3,
            "their_current_rank": 1, "their_review_gain": 28, "their_rating": 4.8,
        },
        "category_health": {
            "score": 4, "position_summary": "needs attention",
            "your_reviews": 89, "category_avg_reviews": 210,
            "your_rating": 4.2, "category_avg_rating": 4.6,
        },
        "attribute_parity": {
            "your_attributes": ["Wheelchair accessible entrance"],
            "top3_attributes": ["Wheelchair accessible entrance", "Online appointments", "On-site services",
                                "Identifies as women-owned", "Free consultation"],
            "missing": ["Online appointments", "On-site services", "Identifies as women-owned", "Free consultation"],
            "your_count": 1, "top3_count": 5,
        },
    },
}

AUDIT_TEXT = """SECTION 1 -- WHAT OUR SCAN FOUND
Our scan on March 30, 2026 recorded Cellino Law at position #7 in the New York personal injury lawyer Google Maps pack, down from #3 the prior week. Morgan & Morgan gained 12 new reviews during this period while Cellino Law's profile showed zero new reviews.

SECTION 2 -- PROBABLE CAUSES
- [HIGH CONFIDENCE] Morgan & Morgan gained 28 reviews over 6 weeks while Cellino Law gained only 4. Google's algorithm heavily weights review recency and velocity.
- [HIGH CONFIDENCE] Three competitors ranked above now have higher ratings (avg 4.6 vs your 4.2). Rating gaps correlate with ranking gaps.
- [MEDIUM CONFIDENCE] Profile completeness: Cellino Law is missing 4 profile attributes that Top 3 competitors have enabled.

SECTION 3 -- PRIORITY ACTIONS
Action 1: Text your 5 most recent clients a direct Google review link today. Why now: your review velocity is 0.8/week vs the 3.0/week benchmark. Effort: Low. Expected impact: High.
Action 2: Add missing profile attributes (Online appointments, Free consultation) to your GBP. Why now: Top 3 all have these. Effort: Low. Expected impact: Medium.
Action 3: Respond to every existing Google review within 24 hours. Why now: engagement signals boost rankings. Effort: Low. Expected impact: Medium.

SECTION 4 -- DO THIS TODAY
SMS Template 1: "Hi [Name], thank you for choosing Cellino Law. If we helped you, a quick Google review means a lot: [link]"
SMS Template 2: "Hi [Name], your feedback helps other New Yorkers find the right attorney. Leave a review here: [link]"
GBP Post: "Cellino Law has served New York's personal injury clients for over 20 years. Free consultations available -- call today or book online through our profile."

SECTION 5 -- CONFIDENCE NOTE
Confidence score: 8/10. Six weeks of tracking data provides strong directional signals. The declining trend is sustained, not a one-week fluctuation."""


def san(text: str) -> str:
    """Sanitize for latin-1 PDF encoding."""
    reps = {"\u2014": "--", "\u2013": "-", "\u2018": "'", "\u2019": "'",
            "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u2022": "-", "\u00a0": " "}
    for c, r in reps.items():
        text = text.replace(c, r)
    return text.encode("latin-1", errors="replace").decode("latin-1")


# ── Chart builders ────────────────────────────────────────────────────────────

def _chart_ranking_trend(history, direction):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker

    fig, ax = plt.subplots(figsize=(6.5, 2.4), dpi=180)
    fig.patch.set_facecolor("#f0f3f8")
    ax.set_facecolor("#f0f3f8")
    weeks = list(range(1, len(history) + 1))
    colors = {"declining": "#dc3545", "volatile": "#fd7e14", "improving": "#28a745", "stable": "#007bff"}
    lc = colors.get(direction, "#007bff")
    ax.invert_yaxis()
    ax.fill_between(weeks, history, max(history) + 1, alpha=0.10, color=lc)
    ax.plot(weeks, history, color=lc, lw=3, marker="o", ms=10, mfc="white", mew=3, mec=lc, zorder=5)
    for w, r in zip(weeks, history):
        c = "#28a745" if r <= 3 else "#dc3545" if r >= 7 else "#333"
        ax.annotate(f"#{r}", (w, r), textcoords="offset points", xytext=(0, -20), ha="center", fontsize=10, fontweight="bold", color=c)
    ax.axhspan(0.5, 3.5, alpha=0.08, color="#28a745", zorder=0)
    ax.text(len(weeks) + 0.3, 2, "Top 3\n(visible)", fontsize=8, color="#28a745", ha="left", va="center", style="italic")
    ax.set_xlabel("Week", fontsize=10, color="#666"); ax.set_ylabel("Rank", fontsize=10, color="#666")
    ax.set_xticks(weeks); ax.set_xticklabels([f"Wk {w}" for w in weeks], fontsize=9)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.set_xlim(0.5, len(weeks) + 0.8); ax.set_ylim(max(history) + 1, 0.5)
    for s in ["top", "right"]: ax.spines[s].set_visible(False)
    for s in ["left", "bottom"]: ax.spines[s].set_color("#ccc")
    ax.tick_params(labelsize=9, colors="#999"); ax.grid(axis="y", alpha=0.15, ls="--")
    fig.tight_layout(pad=0.5)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    fig.savefig(tmp.name, bbox_inches="tight", facecolor="#f0f3f8"); plt.close(fig)
    return tmp.name


def _chart_review_velocity(vel, your_rev, avg_rev):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.5, 1.8), dpi=180, gridspec_kw={"width_ratios": [1, 1.2]})
    fig.patch.set_facecolor("#f0f3f8"); ax1.set_facecolor("#f0f3f8"); ax2.set_facecolor("#f0f3f8")
    target = 3.0
    bc = "#dc3545" if vel < 1.5 else "#fd7e14" if vel < 3 else "#28a745"
    ax1.barh(0, vel, height=0.45, color=bc, zorder=3, edgecolor="white", lw=0.5)
    ax1.axvline(x=target, color="#007bff", ls="--", lw=2, zorder=4)
    ax1.text(target + 0.1, 0.35, f"Target: {target}/wk", fontsize=8, color="#007bff", va="bottom")
    ax1.set_xlim(0, max(target * 1.5, vel * 1.3)); ax1.set_yticks([])
    ax1.set_xlabel("Reviews/Week", fontsize=9, color="#666")
    ax1.set_title("Your Review Velocity", fontsize=10, fontweight="bold", color="#333", pad=10)
    for s in ["top", "right", "left"]: ax1.spines[s].set_visible(False)
    ax1.spines["bottom"].set_color("#ccc"); ax1.tick_params(labelsize=8, colors="#999")

    labels = ["You", "Market Avg"]
    vals = [your_rev, avg_rev]
    cs = ["#dc3545" if your_rev < avg_rev * 0.7 else "#fd7e14" if your_rev < avg_rev else "#28a745", "#007bff"]
    bars = ax2.barh(labels, vals, height=0.55, color=cs, zorder=3, edgecolor="white", lw=0.5)
    for bar, val in zip(bars, vals):
        ax2.text(val + max(vals) * 0.02, bar.get_y() + bar.get_height() / 2, str(val), va="center", fontsize=10, fontweight="bold", color="#333")
    ax2.set_xlim(0, max(vals) * 1.25)
    ax2.set_title("Total Reviews vs Market", fontsize=10, fontweight="bold", color="#333", pad=10)
    for s in ["top", "right"]: ax2.spines[s].set_visible(False)
    for s in ["left", "bottom"]: ax2.spines[s].set_color("#ccc")
    ax2.tick_params(labelsize=9, colors="#999"); ax2.invert_yaxis()
    fig.tight_layout(pad=1.0)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    fig.savefig(tmp.name, bbox_inches="tight", facecolor="#f0f3f8"); plt.close(fig)
    return tmp.name


def _chart_competitor(yr, yrat, yrev, cn, cr, crat, crev):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fig, axes = plt.subplots(1, 3, figsize=(6.5, 2.0), dpi=180)
    fig.patch.set_facecolor("#f0f3f8")
    cats = ["Rank", "Rating", "Review Gain"]
    yv = [yr, yrat, yrev]; cv = [cr, crat, crev]
    for ax, cat, y, c in zip(axes, cats, yv, cv):
        ax.set_facecolor("#f0f3f8")
        x = np.array([0, 0.7])
        bars = ax.bar(x, [y, c], width=0.5, color=["#333", "#007bff"], zorder=3, edgecolor="white", lw=0.5)
        for bar, val in zip(bars, [y, c]):
            lb = f"#{val}" if cat == "Rank" else str(val)
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(y, c) * 0.06, lb, ha="center", va="bottom", fontsize=10, fontweight="bold", color="#333")
        ax.set_xticks(x); ax.set_xticklabels(["You", san(cn[:10])], fontsize=8, color="#666")
        ax.set_title(cat, fontsize=10, fontweight="bold", color="#333", pad=8)
        ax.set_ylim(0, max(y, c) * 1.25 if max(y, c) > 0 else 1)
        for s in ["top", "right"]: ax.spines[s].set_visible(False)
        for s in ["left", "bottom"]: ax.spines[s].set_color("#ccc")
        ax.tick_params(axis="y", labelsize=8, colors="#999")
    fig.tight_layout(pad=1.0)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    fig.savefig(tmp.name, bbox_inches="tight", facecolor="#f0f3f8"); plt.close(fig)
    return tmp.name


def _chart_gauge(score):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fig, ax = plt.subplots(figsize=(3.0, 1.8), dpi=180, subplot_kw={"projection": "polar"})
    fig.patch.set_alpha(0)
    theta_bg = np.linspace(np.pi, 0, 100)
    ax.fill_between(theta_bg, 0.6, 1.0, alpha=0.06, color="#999")
    segs = [(np.linspace(np.pi, np.pi * 0.7, 30), "#dc3545"), (np.linspace(np.pi * 0.7, np.pi * 0.3, 40), "#fd7e14"), (np.linspace(np.pi * 0.3, 0, 30), "#28a745")]
    for t, c in segs:
        ax.plot(t, [0.95] * len(t), color=c, lw=10, alpha=0.35)
    na = np.pi * (1 - score / 10)
    ax.annotate("", xy=(na, 0.85), xytext=(na, 0.2), arrowprops=dict(arrowstyle="->", color="#0f0f0f", lw=2.5))
    c = "#dc3545" if score < 4 else "#fd7e14" if score < 7 else "#28a745"
    ax.text(np.pi / 2, 0.15, f"{score}/10", ha="center", va="center", fontsize=24, fontweight="bold", color=c, transform=ax.transData)
    ax.text(np.pi, 0.55, "0", ha="center", fontsize=8, color="#999")
    ax.text(0, 0.55, "10", ha="center", fontsize=8, color="#999")
    ax.set_ylim(0, 1.05); ax.set_thetamin(0); ax.set_thetamax(180); ax.axis("off")
    fig.tight_layout(pad=0)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    fig.savefig(tmp.name, bbox_inches="tight", transparent=True); plt.close(fig)
    return tmp.name


# ── PDF helpers ───────────────────────────────────────────────────────────────

def _full_bleed(pdf, y, h, color):
    """Draw a full-width colored rectangle."""
    pdf.set_fill_color(*color)
    pdf.rect(0, y, 210, h, "F")


def _accent_bar(pdf, x, y, h, color=ACCENT, width=3):
    pdf.set_fill_color(*color)
    pdf.rect(x, y, width, h, "F")


def _page_header(pdf, w, title, date):
    _full_bleed(pdf, 10, 22, SECTION_BG)
    _accent_bar(pdf, 0, 10, 22, ACCENT, 4)
    pdf.set_xy(20, 13)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*NAVY)
    pdf.cell(w * 0.7, 8, title)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*MID_GRAY)
    pdf.cell(w * 0.3, 8, date, align="R")
    pdf.set_xy(20, 33)


def _section_heading(pdf, w, title):
    y = pdf.get_y()
    _accent_bar(pdf, 20, y, 7, ACCENT)
    pdf.set_xy(26, y)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*NAVY)
    pdf.cell(w - 6, 7, title.upper(), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)


def _metric_card(pdf, x, y, w_card, label, value, color):
    pdf.set_fill_color(*OFF_WHITE)
    pdf.set_draw_color(*LIGHT_GRAY)
    pdf.rect(x, y, w_card, 24, "DF")
    _accent_bar(pdf, x, y, 24, color, 3)
    pdf.set_xy(x + 6, y + 3)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*MID_GRAY)
    pdf.cell(w_card - 10, 4, label)
    pdf.set_xy(x + 6, y + 10)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*color)
    pdf.cell(w_card - 10, 10, value)


def _footer(pdf, w):
    pdf.set_y(-20)
    pdf.set_draw_color(*LIGHT_GRAY)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*MID_GRAY)
    pdf.cell(w, 4, "Search Sentinel  |  sutraflow.org/sentinel  |  Automated Local Search Intelligence", align="C", new_x="LMARGIN", new_y="NEXT")
    
    footer_text = "Rank data is collected via automated public data API. All metrics are computed by our rules engine before this report is generated. Language is produced by AI from pre-computed facts only. This report does not represent an affiliation with or endorsement by Google LLC. Google does not share algorithm details; all probable causes are based on publicly observable correlation data only."
    pdf.set_font("Helvetica", "I", 6)
    pdf.set_text_color(*LIGHT_GRAY)
    pdf.ln(2)
    pdf.multi_cell(w, 3, san(footer_text), align="C")


# ── Build PDF ─────────────────────────────────────────────────────────────────

def build():
    a = ALERT
    ins = a["insights"]
    date_str = datetime.now().strftime("%B %d, %Y")
    w = 170

    # Generate charts
    trend_img = _chart_ranking_trend(ins["rank_trend"]["history"], ins["rank_trend"]["direction"])
    rv_img = _chart_review_velocity(ins["review_velocity"]["reviews_per_week"], ins["category_health"]["your_reviews"], ins["category_health"]["category_avg_reviews"])
    comp_img = _chart_competitor(a["curr_rank"], a["rating"], max(a["reviews"] - a["prev_reviews"], 0), ins["competitor_spotlight"]["fastest_climber"], ins["competitor_spotlight"]["their_current_rank"], ins["competitor_spotlight"]["their_rating"], ins["competitor_spotlight"]["their_review_gain"])
    gauge_img = _chart_gauge(ins["category_health"]["score"])

    pdf = FPDF()
    pdf.set_left_margin(20); pdf.set_right_margin(20)
    pdf.set_auto_page_break(auto=False)

    # ═══ PAGE 1 — COVER ═══════════════════════════════════════════════════
    pdf.add_page()
    _full_bleed(pdf, 0, 85, NAVY)
    _full_bleed(pdf, 85, 4, ACCENT)

    # Brand
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 30)
    pdf.set_xy(20, 16)
    pdf.cell(w, 12, san("Search Sentinel"))
    pdf.set_font("Helvetica", "", 12)
    pdf.set_xy(20, 33)
    pdf.set_text_color(160, 180, 210)
    pdf.cell(w, 7, san("Local Search Intelligence & Monitoring"))
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_xy(20, 50)
    pdf.set_text_color(*WHITE)
    pdf.cell(w, 8, san("Recovery Blueprint"))
    pdf.set_font("Helvetica", "", 10)
    pdf.set_xy(20, 64)
    pdf.set_text_color(140, 160, 190)
    pdf.cell(w, 6, date_str)

    # Confidence badge in header corner
    cs = a.get("_confidence_score", 8)
    pdf.set_xy(155, 50)
    pdf.set_fill_color(*ACCENT)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(35, 7, san(f"  Confidence: {cs}/10"), fill=True)

    # Business details
    pdf.set_xy(20, 100)
    pdf.set_text_color(*MID_GRAY)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(w, 6, san("PREPARED FOR"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_text_color(*NAVY)
    pdf.multi_cell(w, 11, san(a["business_name"]), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(*MID_GRAY)
    pdf.cell(w, 7, san("Personal Injury Lawyer  |  New York, NY"), new_x="LMARGIN", new_y="NEXT")

    # Metric cards — row 1
    pdf.ln(10)
    y = pdf.get_y()
    card_w = w / 3 - 2
    _metric_card(pdf, 20, y, card_w, "CURRENT RANK", f"#{a['curr_rank']}", RED)
    _metric_card(pdf, 20 + card_w + 3, y, card_w, "PREVIOUS RANK", f"#{a['prev_rank']}", DARK_GRAY)
    _metric_card(pdf, 20 + 2 * (card_w + 3), y, card_w, "POSITIONS LOST", str(a["rank_change"]), RED)
    pdf.set_y(y + 28)

    # Metric cards — row 2
    y = pdf.get_y()
    _metric_card(pdf, 20, y, card_w, "RATING", f"{a['rating']} stars", DARK_GRAY)
    _metric_card(pdf, 20 + card_w + 3, y, card_w, "REVIEWS", str(a["reviews"]), DARK_GRAY)
    _metric_card(pdf, 20 + 2 * (card_w + 3), y, card_w, "WEEKS TRACKED", str(a["weeks_tracked"]), ACCENT)
    pdf.set_y(y + 28)

    # Status bar
    badge = f"Week {a['weeks_tracked']}  |  {ins['review_velocity']['reviews_per_week']} reviews/wk  |  Trend: {ins['rank_trend']['direction']}  |  Health: {ins['category_health']['score']}/10"
    pdf.set_fill_color(*SECTION_BG)
    pdf.set_draw_color(*LIGHT_GRAY)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(*MID_GRAY)
    pdf.cell(w, 10, san(badge), border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")

    _footer(pdf, w)

    # ═══ PAGE 2 — EXECUTIVE SUMMARY ═══════════════════════════════════════
    pdf.add_page()
    _page_header(pdf, w, "Executive Summary", date_str)

    # Alert banner
    _full_bleed(pdf, pdf.get_y(), 12, RED_BG)
    _accent_bar(pdf, 0, pdf.get_y(), 12, RED, 4)
    pdf.set_xy(24, pdf.get_y() + 2)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*RED)
    pdf.cell(w, 8, san("ALERT: RANKING DECLINE DETECTED -- 4 POSITIONS LOST"))
    pdf.set_y(pdf.get_y() + 14)

    # What happened
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*DARK_GRAY)
    pdf.multi_cell(w, 5.5, san(
        "Our scan on March 30, 2026 recorded Cellino Law at position #7 in the New York "
        "personal injury lawyer Google Maps pack, down from #3 the prior week. Morgan and Morgan "
        "gained 12 new reviews during this period while Cellino Law's profile showed zero new reviews."
    ), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Impact callout
    _full_bleed(pdf, pdf.get_y(), 26, AMBER_BG)
    _accent_bar(pdf, 0, pdf.get_y(), 26, AMBER, 4)
    iy = pdf.get_y()
    pdf.set_xy(24, iy + 2)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(146, 64, 14)
    pdf.cell(w, 6, san("BUSINESS IMPACT"))
    pdf.set_xy(24, iy + 9)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(w - 8, 5, san(
        "Position #7 is below the fold on mobile. Google data shows 90% of clicks go to "
        "the top 3. At position #7, you are losing an estimated 88% of potential leads."
    ))
    pdf.set_y(iy + 28)
    pdf.ln(2)

    # Root Cause Analysis
    _section_heading(pdf, w, "Root Cause Analysis")
    causes = [
        ("[HIGH CONFIDENCE] Morgan and Morgan gained 28 reviews over 6 weeks while Cellino Law gained only 4.", RED),
        ("[HIGH CONFIDENCE] Three competitors have higher ratings (avg 4.6 vs your 4.2).", RED),
        ("[MEDIUM CONFIDENCE] Profile missing 4 attributes that Top 3 competitors have enabled.", AMBER),
    ]
    for text, color in causes:
        y = pdf.get_y()
        _accent_bar(pdf, 22, y, 12, color, 2)
        pdf.set_xy(27, y)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*DARK_GRAY)
        pdf.multi_cell(w - 10, 5.5, san(text), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
    pdf.ln(2)

    # Priority Actions
    _section_heading(pdf, w, "Priority Actions")
    actions = [
        ("1", "Text your 5 most recent clients a direct Google review link today.", "Review velocity is 0.8/wk vs 3.0/wk benchmark", "Low", "High"),
        ("2", "Add missing profile attributes to your GBP (Online appointments, Free consultation).", "Top 3 all have these enabled", "Low", "Medium"),
        ("3", "Respond to every existing Google review within 24 hours.", "Engagement signals boost local rankings", "Low", "Medium"),
    ]
    for num, action, why, effort, impact in actions:
        y = pdf.get_y()
        pdf.set_fill_color(*ACCENT)
        pdf.set_text_color(*WHITE)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_xy(20, y)
        pdf.cell(8, 8, num, fill=True, align="C")
        pdf.set_xy(30, y)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*NAVY)
        pdf.multi_cell(w - 12, 5, san(action), new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(30)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(*MID_GRAY)
        pdf.cell(w - 12, 4, san(f"Why now: {why}  |  Effort: {effort}  |  Impact: {impact}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

    _footer(pdf, w)

    # ═══ PAGE 3 — PERFORMANCE INTELLIGENCE ════════════════════════════════
    pdf.add_page()
    _page_header(pdf, w, "Performance Intelligence", date_str)

    _section_heading(pdf, w, san("Ranking Trend -- 6 Week History"))
    pdf.image(trend_img, x=18, y=pdf.get_y(), w=w + 4)
    pdf.ln(56)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*DARK_GRAY)
    pdf.multi_cell(w, 5, san(
        "Your ranking is in a sustained decline from #3 to #7 over six weeks. This is not "
        "a temporary fluctuation. Immediate action is needed to reverse this trajectory before "
        "it becomes your new baseline position."
    ), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    _section_heading(pdf, w, "Review Performance")
    pdf.image(rv_img, x=18, y=pdf.get_y(), w=w + 4)
    pdf.ln(44)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*DARK_GRAY)
    pdf.multi_cell(w, 5, san(
        "Your review growth has stalled at 0.8 reviews/week. According to BrightLocal's 2025 "
        "survey, 87% of consumers read online reviews before visiting a local business. "
        "Businesses averaging 3+ reviews/week consistently outrank those below 1/week."
    ), new_x="LMARGIN", new_y="NEXT")

    _footer(pdf, w)

    # ═══ PAGE 4 — COMPETITIVE ANALYSIS ════════════════════════════════════
    pdf.add_page()
    _page_header(pdf, w, "Competitive Analysis", date_str)

    cs = ins["competitor_spotlight"]
    _section_heading(pdf, w, f"You vs {san(cs['fastest_climber'])}")
    pdf.image(comp_img, x=18, y=pdf.get_y(), w=w + 4)
    pdf.ln(48)

    # Competitor table
    pdf.set_fill_color(*NAVY)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*WHITE)
    pdf.cell(60, 8, "  Metric", fill=True, border=1)
    pdf.cell(55, 8, "Cellino Law", fill=True, border=1, align="C")
    pdf.cell(55, 8, san(cs["fastest_climber"]), fill=True, border=1, align="C")
    pdf.ln()
    rows = [
        ("Current Rank", f"#{a['curr_rank']}", f"#{cs['their_current_rank']}"),
        ("Rating", f"{a['rating']}", f"{cs['their_rating']}"),
        ("Review Gain (6wk)", f"+{a['reviews'] - a['prev_reviews']}", f"+{cs['their_review_gain']}"),
        ("Momentum", "Declining", f"Climbing (+{cs['climbed_positions']})"),
    ]
    for i, (label, yv, cv) in enumerate(rows):
        bg = OFF_WHITE if i % 2 == 0 else WHITE
        pdf.set_fill_color(*bg)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*DARK_GRAY)
        pdf.cell(60, 7, f"  {label}", fill=True, border=1)
        pdf.set_text_color(*NAVY)
        pdf.cell(55, 7, yv, fill=True, border=1, align="C")
        pdf.set_text_color(*ACCENT)
        pdf.cell(55, 7, cv, fill=True, border=1, align="C")
        pdf.ln()
    pdf.ln(6)

    # Health gauge
    ch = ins["category_health"]
    _section_heading(pdf, w, "Market Position Score")
    gy = pdf.get_y()
    pdf.image(gauge_img, x=22, y=gy, w=52)
    pdf.set_xy(80, gy + 4)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*DARK_GRAY)
    metrics = [
        f"Your reviews: {ch['your_reviews']}  |  Market avg: {ch['category_avg_reviews']}",
        f"Your rating: {ch['your_rating']}  |  Market avg: {ch['category_avg_rating']}",
        f"Position: {ch['position_summary'].title()}",
    ]
    for i, m in enumerate(metrics):
        pdf.set_xy(80, gy + 6 + i * 8)
        pdf.cell(90, 6, m)
    pdf.set_y(gy + 36)

    # Attribute Parity
    ap = ins.get("attribute_parity", {})
    if ap.get("missing"):
        pdf.ln(4)
        _section_heading(pdf, w, "Profile Attribute Gap Analysis")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*MID_GRAY)
        pdf.multi_cell(w, 5, san(
            f"You have {ap['your_count']} profile attributes. Top 3 competitors average "
            f"{ap['top3_count']}. Missing attributes cost visibility in filtered searches."
        ), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

        col_a, col_y, col_t = w * 0.6, w * 0.2, w * 0.2
        pdf.set_fill_color(*NAVY)
        pdf.set_text_color(*WHITE)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(col_a, 7, "  Attribute", fill=True, border=1)
        pdf.cell(col_y, 7, "You", fill=True, border=1, align="C")
        pdf.cell(col_t, 7, "Top 3", fill=True, border=1, align="C")
        pdf.ln()
        pdf.set_font("Helvetica", "", 8)
        for i, attr in enumerate(ap["missing"][:6]):
            bg = OFF_WHITE if i % 2 == 0 else WHITE
            pdf.set_fill_color(*bg)
            pdf.set_text_color(*DARK_GRAY)
            pdf.cell(col_a, 6, f"  {san(attr)}", fill=True, border=1)
            pdf.set_text_color(*RED)
            pdf.cell(col_y, 6, "Missing", fill=True, border=1, align="C")
            pdf.set_text_color(*GREEN)
            pdf.cell(col_t, 6, "Yes", fill=True, border=1, align="C")
            pdf.ln()

    _footer(pdf, w)

    # ═══ PAGE 5 — GBP CHECKLIST ═══════════════════════════════════════════
    pdf.add_page()
    _page_header(pdf, w, "Google Business Profile Checklist", date_str)

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*MID_GRAY)
    pdf.multi_cell(w, 5, san(
        "GBP signals account for ~33% of local pack ranking factors (Whitespark 2025). "
        "Complete every item below to maximize your profile strength."
    ), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    checklist = [
        ("Profile Completeness", [
            "Business name matches real-world signage exactly",
            "Primary category is the most specific match",
            "2-3 secondary categories added",
            "Description uses target keywords naturally (750 chars max)",
            "Service area and address are accurate",
            "Phone matches website contact page",
            "Website URL points to most relevant landing page",
            "Business hours accurate including holidays",
        ]),
        ("Photos and Media", [
            "At least 10 high-quality photos uploaded",
            "Cover photo is professional (updated within 6 months)",
            "Team/staff photos added",
            "1-2 new photos added every week",
        ]),
        ("Reviews and Reputation", [
            f"Review count: {a['reviews']} -- aim for {max(a['reviews'] + 50, 200)}+",
            "Respond to every review within 24 hours",
            "Active review-request process after every job",
            "No incentivized reviews (violates Google policy)",
        ]),
        ("Posts and Engagement", [
            "Google Post published at least once per week",
            "Q and A section pre-populated with common questions",
            "Products/Services section filled with descriptions",
            "CTA button included on every post",
        ]),
    ]
    for sec_title, items in checklist:
        y = pdf.get_y()
        _full_bleed(pdf, y, 8, SECTION_BG)
        _accent_bar(pdf, 0, y, 8, ACCENT, 4)
        pdf.set_xy(24, y + 1)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*NAVY)
        pdf.cell(w, 6, sec_title)
        pdf.set_y(y + 10)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*DARK_GRAY)
        for item in items:
            pdf.set_x(26)
            pdf.cell(4, 5, "[ ]")
            pdf.set_x(32)
            pdf.cell(w - 14, 5, san(item), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

    _footer(pdf, w)

    # ═══ PAGE 6 — DO THIS TODAY ═══════════════════════════════════════════
    pdf.add_page()
    _page_header(pdf, w, san("Do This Today -- Ready-to-Use Assets"), date_str)

    _section_heading(pdf, w, "SMS Review Request Templates")
    templates = [
        '"Hi [Name], thank you for choosing Cellino Law. If we helped you, a quick Google review means a lot: [link]"',
        '"Hi [Name], your feedback helps other New Yorkers find the right attorney. Leave a review here: [link]"',
    ]
    for i, t in enumerate(templates, 1):
        y = pdf.get_y()
        _full_bleed(pdf, y, 14, OFF_WHITE)
        _accent_bar(pdf, 18, y, 14, ACCENT, 2)
        pdf.set_xy(24, y + 2)
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*DARK_GRAY)
        pdf.multi_cell(w - 8, 5, san(f"Template {i}: {t}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
    pdf.ln(4)

    _section_heading(pdf, w, "Google Business Profile Post Draft")
    y = pdf.get_y()
    _full_bleed(pdf, y, 16, GREEN_BG)
    _accent_bar(pdf, 18, y, 16, GREEN, 2)
    pdf.set_xy(24, y + 2)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*DARK_GRAY)
    pdf.multi_cell(w - 8, 5, san(
        '"Cellino Law has served New York\'s personal injury clients for over 20 years. '
        'Free consultations available -- call today or book online through our profile."'
    ), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)

    # ═══ PAGE 7 — CTA / PRICING ══════════════════════════════════════════
    _section_heading(pdf, w, "What Happens Next")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*DARK_GRAY)
    pdf.multi_cell(w, 5.5, san(
        "This blueprint identified specific actions to recover your ranking. Local Visibility is a "
        "continuous process -- businesses that monitor weekly consistently outperform those "
        "that react only after significant drops."
    ), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)



    # Service (highlighted)
    y = pdf.get_y()
    pdf.set_fill_color(*NAVY)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*WHITE)
    pdf.cell(w, 9, san("  Map Pack Guardian -- Weekly Monitoring"), fill=True, border="LTR", new_x="LMARGIN", new_y="NEXT")
    pdf.set_fill_color(230, 240, 255)
    pdf.set_text_color(*DARK_GRAY)
    pdf.set_font("Helvetica", "", 9)
    for item in ["Weekly rank tracking with instant drop alerts", "Competitor movement intelligence", "Review velocity monitoring", "Monthly trend reports with charts"]:
        pdf.cell(w, 6, f"    {item}", fill=True, border="LR", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*ACCENT)
    pdf.cell(w, 9, san("  $5/month -- Cancel anytime"), fill=True, border="LBR", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(6)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*MID_GRAY)
    pdf.multi_cell(w, 5, san("Reply to this email to get started, or visit sutraflow.org/sentinel"), new_x="LMARGIN", new_y="NEXT")

    _footer(pdf, w)

    # Save
    out = Path.home() / "Desktop" / "sample_audit_report_v4.pdf"
    pdf.output(str(out))
    print(f"Generated: {out} ({out.stat().st_size / 1024:.1f} KB)")

    # Cleanup temp chart files
    for f in [trend_img, rv_img, comp_img, gauge_img]:
        try: Path(f).unlink(missing_ok=True)
        except: pass

    return out


if __name__ == "__main__":
    build()
