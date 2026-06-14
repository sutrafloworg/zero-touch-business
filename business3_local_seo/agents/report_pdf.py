"""
report_pdf — compact, data-dense PDF layout for Search Sentinel audit reports.

Why this module exists
----------------------
The previous renderer in report_agent._build_pdf had two structural problems:

1. BLANK PAGES. It added a "Performance Intelligence" page whenever
   ``weeks_tracked > 1`` (line: ``if has_insights or weeks > 1``) but only drew
   content when ``insights`` contained computed charts. Real early-stage leads
   carry ``weeks_tracked == 2`` and ``insights == {}`` (see data/pending_reports.json),
   so the page header was drawn and nothing followed — a guaranteed blank page.

2. THIN, AIRY LAYOUT. The cover used ~100mm before any content; each analysis
   section sat on its own near-empty page. Customers paid for "blank-ish" PDFs.

This renderer fixes both:

* Every page is gated on real content, so a blank page is impossible.
* Two dense pages for the common case; a third only when a multi-week rank
  trend chart actually exists.
* It mines the data that already exists but was never used — the full Local
  Pack from data/rankings_history.json — to build a per-business competitor
  leaderboard. That works for *every* lead, even at week 2 with no insights,
  and is the single most useful, specific thing in the report.

The function ``build_report_pdf`` is a drop-in replacement called by
``ReportAgent.generate_audit``.
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

logger = logging.getLogger(__name__)

# ── Palette ───────────────────────────────────────────────────────────────────
BLACK = (15, 18, 24)
WHITE = (255, 255, 255)
DARK_GRAY = (48, 54, 64)
MID_GRAY = (108, 116, 128)
LIGHT_GRAY = (198, 204, 212)
HAIRLINE = (228, 232, 238)
ACCENT = (0, 102, 204)
ACCENT_DK = (0, 74, 150)
RED_TEXT = (188, 36, 36)
AMBER_BG = (255, 247, 230)
AMBER_LINE = (217, 145, 20)
AMBER_TEXT = (140, 84, 10)
GREEN_TEXT = (24, 120, 52)
SECTION_BG = (246, 248, 251)
SUBJECT_BG = (255, 244, 224)

MARGIN = 15
CONTENT_W = 210 - 2 * MARGIN  # 180


# ── Text helpers ──────────────────────────────────────────────────────────────
_REPLACEMENTS = {
    "—": "--", "–": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", "•": "-",
    " ": " ", "→": "->", "←": "<-", "‣": ">",
    "●": "*", "★": "*", "⭐": "*",
}


def _san(text) -> str:
    """Make text safe for the latin-1 core fonts fpdf2 ships with."""
    s = str(text)
    for ch, rep in _REPLACEMENTS.items():
        s = s.replace(ch, rep)
    return s.encode("latin-1", errors="replace").decode("latin-1")


def _truncate(s: str, n: int) -> str:
    s = _san(s)
    return s if len(s) <= n else s[: max(n - 3, 1)].rstrip() + "..."


def _clean_bullet(line: str) -> str:
    """Strip leading list markers so we can render our own."""
    line = _san(line).strip()
    for prefix in ("- ", "* ", "• "):
        if line.startswith(prefix):
            line = line[len(prefix):].strip()
            break
    # strip "1. ", "2) " etc.
    i = 0
    while i < len(line) and line[i].isdigit():
        i += 1
    if i > 0 and i < len(line) and line[i] in ".)":
        line = line[i + 1:].strip()
    # strip a redundant leading "Action:" label from Claude's quick-wins format
    if line[:7].lower() == "action:":
        line = line[7:].strip()
    return line


def _parse_sections(audit_text: str) -> dict:
    """Parse Claude's labelled sections. Mirrors report_agent._parse_audit_sections."""
    sections: dict[str, list[str]] = {}
    current = None
    headers = ("what happened", "why", "quick wins", "your trend",
               "competitor to watch", "your standing")
    for line in (audit_text or "").split("\n"):
        stripped = line.strip()
        matched = False
        for h in headers:
            if stripped.upper().startswith(h.upper()):
                current = h
                rem = stripped[len(h):].strip().lstrip(":").lstrip("-").strip()
                sections[current] = [rem] if rem else []
                matched = True
                break
        if not matched and current is not None:
            sections[current].append(line)
    return {k: [ln for ln in v] for k, v in sections.items()}


# ── Competitor data (the previously-unused goldmine) ──────────────────────────
def _load_competitors(alert: dict, rankings_file) -> tuple[list[dict], dict | None]:
    """Return (sorted local pack, subject row or None).

    Prefers competitors embedded on the alert; otherwise reads the latest
    snapshot for this category from rankings_history.json. Works for any lead.
    """
    comps = alert.get("_competitors") or alert.get("competitors")
    if not comps and rankings_file:
        try:
            data = json.loads(Path(rankings_file).read_text(encoding="utf-8"))
            entry = data.get(alert.get("category_key", ""), {})
            snaps = entry.get("snapshots")
            comps = (snaps[-1]["results"] if snaps else entry.get("results", []))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"report_pdf: could not load competitors: {e}")
            comps = []

    norm = []
    for c in comps or []:
        try:
            norm.append({
                "rank": int(c.get("rank", 99)),
                "name": _san(c.get("name", "")),
                "rating": c.get("rating", 0),
                "reviews": int(c.get("reviews", 0) or 0),
            })
        except Exception:  # noqa: BLE001
            continue
    norm.sort(key=lambda x: x["rank"])

    subject = None
    bn = _san(alert.get("business_name", "")).lower()
    for c in norm:
        if c["name"].lower() == bn:
            subject = c
            break
    if subject is None:
        for c in norm:
            if c["rank"] == alert.get("curr_rank"):
                subject = c
                break
    return norm, subject


def _trend_chart(history: list[int], direction: str):
    """Self-contained matplotlib rank-trend chart. Returns a temp PNG path or None.

    Kept independent of report_agent so this module has no heavy (anthropic)
    import path and degrades gracefully if matplotlib is unavailable.
    """
    try:
        import tempfile
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.ticker as ticker

        colors = {"declining": "#bc2424", "volatile": "#d99114",
                  "improving": "#187834", "stable": "#0066cc"}
        line_color = colors.get(direction, "#0066cc")
        weeks = list(range(1, len(history) + 1))

        fig, ax = plt.subplots(figsize=(6.6, 2.0), dpi=150)
        ax.invert_yaxis()
        ax.fill_between(weeks, history, max(history) + 1, alpha=0.07, color=line_color)
        ax.plot(weeks, history, color=line_color, linewidth=2.4, marker="o",
                markersize=7, markerfacecolor="white", markeredgewidth=2.2,
                markeredgecolor=line_color, zorder=5)
        for wv, rv in zip(weeks, history):
            c = "#187834" if rv <= 3 else "#bc2424" if rv >= 7 else "#333333"
            ax.annotate(f"#{rv}", (wv, rv), textcoords="offset points", xytext=(0, -16),
                        ha="center", fontsize=8.5, fontweight="bold", color=c)
        ax.axhspan(0.5, 3.5, alpha=0.06, color="#187834", zorder=0)
        ax.text(len(weeks) + 0.25, 2, "Top 3", fontsize=7, color="#187834",
                ha="left", va="center", style="italic")
        ax.set_ylabel("Rank", fontsize=8.5, color="#666666")
        ax.set_xticks(weeks)
        ax.set_xticklabels([f"Wk {w}" for w in weeks], fontsize=8)
        ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
        ax.set_xlim(0.5, len(weeks) + 0.8)
        ax.set_ylim(max(history) + 1, 0.5)
        ax.tick_params(axis="both", labelsize=8, colors="#999999")
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color("#dddddd")
        ax.grid(axis="y", alpha=0.15, linestyle="--")
        fig.tight_layout(pad=0.4)
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        # No bbox_inches="tight": keep the canvas aspect exactly 6.6:2.0 so the
        # caller can place it at a known height without overlap.
        fig.savefig(tmp.name, facecolor="white")
        plt.close(fig)
        return tmp.name
    except Exception as e:  # noqa: BLE001
        logger.warning(f"report_pdf: trend chart unavailable: {e}")
        return None


# ── Low-level draw helpers ────────────────────────────────────────────────────
def _section_heading(pdf: FPDF, title: str):
    y = pdf.get_y()
    pdf.set_fill_color(*ACCENT)
    pdf.rect(MARGIN, y + 0.5, 3, 6, "F")
    pdf.set_xy(MARGIN + 6, y)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*BLACK)
    pdf.cell(CONTENT_W - 6, 7, title.upper(), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.5)


def _metric_strip(pdf: FPDF, y: float, cells: list[tuple], h: float = 19):
    n = len(cells)
    gap = 2.5
    cw = (CONTENT_W - gap * (n - 1)) / n
    for i, (label, value, color) in enumerate(cells):
        cx = MARGIN + i * (cw + gap)
        pdf.set_fill_color(*SECTION_BG)
        pdf.set_draw_color(*HAIRLINE)
        pdf.rect(cx, y, cw, h, "DF")
        pdf.set_xy(cx + 2, y + 3)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*MID_GRAY)
        pdf.cell(cw - 4, 4, _truncate(label, 16))
        pdf.set_xy(cx + 2, y + 8.5)
        pdf.set_font("Helvetica", "B", 15)
        pdf.set_text_color(*color)
        pdf.cell(cw - 4, 8, str(value))
    pdf.set_y(y + h)


def _bullets(pdf: FPDF, lines: list[str], numbered: bool = False, max_items: int = 4):
    pdf.set_font("Helvetica", "", 10)
    shown = 0
    for raw in lines:
        text = _clean_bullet(raw)
        if not text:
            continue
        shown += 1
        if shown > max_items:
            break
        marker = f"{shown}." if numbered else "-"
        y = pdf.get_y()
        pdf.set_xy(MARGIN + 4, y)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*ACCENT)
        pdf.cell(6, 5.4, marker)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*DARK_GRAY)
        pdf.set_xy(MARGIN + 10, y)
        pdf.multi_cell(CONTENT_W - 10, 5.4, text, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1.2)


def _para(pdf: FPDF, lines: list[str]):
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*DARK_GRAY)
    for raw in lines:
        text = _san(raw).strip()
        if text:
            pdf.multi_cell(CONTENT_W, 5.4, text, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def _service_box(pdf: FPDF, x: float, y: float, bw: float, title: str,
                 bullets: list[str], price: str, primary: bool):
    bh = 52
    if primary:
        pdf.set_fill_color(*ACCENT)
        pdf.rect(x, y, bw, 9, "F")
        pdf.set_text_color(*WHITE)
    else:
        pdf.set_fill_color(*SECTION_BG)
        pdf.set_draw_color(*LIGHT_GRAY)
        pdf.rect(x, y, bw, bh, "D")
        pdf.rect(x, y, bw, 9, "F")
        pdf.set_text_color(*BLACK)
    if primary:
        pdf.set_draw_color(*ACCENT)
        pdf.rect(x, y, bw, bh, "D")
    pdf.set_xy(x + 3, y + 1.5)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(bw - 6, 6, _truncate(title, 34))
    pdf.set_font("Helvetica", "", 8.2)
    pdf.set_text_color(*DARK_GRAY)
    ly = y + 12
    for b in bullets:
        pdf.set_xy(x + 3, ly)
        pdf.set_text_color(*ACCENT)
        pdf.cell(3, 4.4, "-")
        pdf.set_text_color(*DARK_GRAY)
        pdf.set_xy(x + 6, ly)
        pdf.multi_cell(bw - 9, 4.4, _truncate(b, 60), new_x="LMARGIN", new_y="NEXT")
        ly = pdf.get_y() + 0.6
    pdf.set_xy(x + 3, y + bh - 9)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*ACCENT_DK if not primary else ACCENT)
    pdf.set_text_color(*ACCENT)
    pdf.cell(bw - 6, 7, price)
    return bh


# ── Main entry point ──────────────────────────────────────────────────────────
def build_report_pdf(reporter, alert: dict, audit_text: str,
                     city: str, state: str, category: str) -> Path:
    """Build a compact, never-blank audit PDF. Returns the written path."""
    city, state, category = _san(city), _san(state), _san(category)
    biz = _san(alert.get("business_name", "Your Business"))
    curr = alert.get("curr_rank", "?")
    prev = alert.get("prev_rank", "?")
    drop = alert.get("rank_change", "?")
    rating = alert.get("rating", "N/A")
    reviews = int(alert.get("reviews", 0) or 0)
    weeks = alert.get("weeks_tracked", 1)
    insights = alert.get("insights", {}) or {}
    reasons = [_san(r) for r in alert.get("reasons", [])]
    report_date = datetime.now().strftime("%B %d, %Y")

    sections = _parse_sections(audit_text)
    rankings_file = getattr(reporter, "rankings_file", None)
    competitors, subject = _load_competitors(alert, rankings_file)

    pdf = FPDF()
    pdf.set_left_margin(MARGIN)
    pdf.set_right_margin(MARGIN)
    pdf.set_auto_page_break(auto=True, margin=15)

    # ═══════════════ PAGE 1 — THE DIAGNOSIS ═══════════════════════════════════
    pdf.add_page()

    # Header band
    pdf.set_fill_color(*BLACK)
    pdf.rect(0, 0, 210, 40, "F")
    pdf.set_fill_color(*ACCENT)
    pdf.rect(0, 40, 210, 2.2, "F")
    pdf.set_xy(MARGIN, 9)
    pdf.set_font("Helvetica", "B", 21)
    pdf.set_text_color(*WHITE)
    pdf.cell(120, 10, "Search Sentinel")
    pdf.set_xy(210 - MARGIN - 60, 12)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(165, 174, 186)
    pdf.cell(60, 5, report_date, align="R")
    pdf.set_xy(210 - MARGIN - 60, 17.5)
    pdf.cell(60, 5, "CONFIDENTIAL", align="R")
    pdf.set_xy(MARGIN, 22)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(150, 160, 174)
    pdf.cell(150, 6, "Local Search Ranking Audit")

    # Subject
    pdf.set_xy(MARGIN, 47)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*MID_GRAY)
    pdf.cell(CONTENT_W, 4.5, "PREPARED FOR", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(MARGIN)
    pdf.set_font("Helvetica", "B", 19)
    pdf.set_text_color(*BLACK)
    pdf.multi_cell(CONTENT_W, 8.5, biz, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(0.5)
    pdf.set_x(MARGIN)
    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_text_color(*MID_GRAY)
    loc = f"{category}  |  {city}, {state}".strip(" |")
    pdf.cell(CONTENT_W, 6, loc, new_x="LMARGIN", new_y="NEXT")

    # Metric strip
    pdf.ln(3)
    rank_color = RED_TEXT if isinstance(curr, int) and curr > 3 else GREEN_TEXT
    _metric_strip(pdf, pdf.get_y(), [
        ("Current Rank", f"#{curr}", rank_color),
        ("Previous Rank", f"#{prev}", DARK_GRAY),
        ("Positions Lost", str(drop), RED_TEXT),
        ("Rating", str(rating), DARK_GRAY),
        ("Reviews", str(reviews), DARK_GRAY),
        ("Weeks Tracked", str(weeks), ACCENT),
    ])

    # Impact line (single dense callout, not a 4-line box)
    pdf.ln(3)
    if isinstance(curr, int) and curr > 3:
        pct = min(95, 60 + (curr - 3) * 8)
        impact = (f"At #{curr} you sit below the top 3 -- where ~90% of Google Maps clicks land. "
                  f"That puts an estimated {pct}% of local search leads out of reach until you recover.")
    elif isinstance(curr, int):
        impact = (f"At #{curr} you are still in the top 3, but the {drop}-position slide is an early "
                  f"warning. Top-3 placements turn over fast when a competitor pushes.")
    else:
        impact = "Your ranking moved against you this period. The detail below shows where and why."
    y0 = pdf.get_y()
    pdf.set_fill_color(*AMBER_BG)
    pdf.set_draw_color(*AMBER_LINE)
    pdf.set_xy(MARGIN, y0)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*AMBER_TEXT)
    # measure height via split_only
    lines = pdf.multi_cell(CONTENT_W - 6, 5, impact, dry_run=True, output="LINES")
    box_h = len(lines) * 5 + 4
    pdf.rect(MARGIN, y0, CONTENT_W, box_h, "DF")
    pdf.set_xy(MARGIN + 3, y0 + 2)
    pdf.multi_cell(CONTENT_W - 6, 5, impact, new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(y0 + box_h + 4)

    # WHAT HAPPENED
    _section_heading(pdf, "What Happened")
    wh = sections.get("what happened")
    if not wh or not any(s.strip() for s in wh):
        first_reason = (" " + reasons[0]) if reasons else ""
        wh = [f"Our {report_date} scan recorded {biz} at position #{curr} for \"{category}\" "
              f"searches in {city}, {state} -- down from #{prev} the prior week, a slide of "
              f"{drop} positions.{first_reason}"]
    _para(pdf, wh)

    # WHY YOU SLIPPED
    _section_heading(pdf, "Why You Slipped")
    why = sections.get("why")
    if not why or not any(s.strip() for s in why):
        why = reasons or [
            "At this rank the most common driver is competitors gaining review volume and recency "
            "faster than you -- the leaderboard on the next page shows exactly who.",
            "A multi-position drop in one week is far more often a competitor's profile change or "
            "review burst than a Google penalty on your listing.",
        ]
    _bullets(pdf, why, numbered=False, max_items=4)

    # DO THIS NEXT
    _section_heading(pdf, "Do This Next")
    quick = sections.get("quick wins")
    if not quick or not any(s.strip() for s in quick):
        quick = [
            "Ask your last 10 happy customers for a Google review this week. Review volume and "
            "recency are among the strongest local-pack signals.",
            "Make your Google Business Profile categories, hours, and service area exactly match "
            "your website -- mismatches quietly suppress ranking.",
            "Post a weekly Google update (offer, photo, or news). Active profiles outrank dormant ones.",
        ]
    _bullets(pdf, quick, numbered=True, max_items=4)

    # Footer note — pin to the bottom of page 1. Auto page-break must be off
    # here, otherwise writing near the bottom margin spills it onto a blank page.
    pdf.set_auto_page_break(False)
    pdf.set_y(-12)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(*LIGHT_GRAY)
    pdf.cell(CONTENT_W, 4, "Data source: Google Maps Local Pack, collected via automated weekly scans. "
             "Rankings vary by device and location.", align="C")
    pdf.set_auto_page_break(auto=True, margin=15)

    # ═══════════════ PAGE 2 — COMPETITIVE LANDSCAPE + NEXT STEPS ══════════════
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*BLACK)
    pdf.cell(CONTENT_W * 0.7, 8, "Your Competitive Landscape")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*MID_GRAY)
    pdf.cell(CONTENT_W * 0.3, 8, report_date, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*ACCENT)
    pdf.set_line_width(0.5)
    pdf.line(MARGIN, pdf.get_y(), 210 - MARGIN, pdf.get_y())
    pdf.set_line_width(0.2)
    pdf.ln(5)

    if competitors:
        _section_heading(pdf, "Who's Winning Your Market")

        # Table header
        c_rank, c_rating, c_rev = 16, 28, 30
        c_name = CONTENT_W - c_rank - c_rating - c_rev
        pdf.set_fill_color(*BLACK)
        pdf.set_text_color(*WHITE)
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.cell(c_rank, 7.5, "  #", fill=True)
        pdf.cell(c_name, 7.5, "  Business", fill=True)
        pdf.cell(c_rating, 7.5, "Rating", fill=True, align="C")
        pdf.cell(c_rev, 7.5, "Reviews", fill=True, align="C")
        pdf.ln()

        rows = competitors[:6]
        if subject and subject not in rows:
            rows = competitors[:5] + [subject]
        for idx, c in enumerate(rows):
            is_subj = subject is not None and c is subject
            if is_subj:
                pdf.set_fill_color(*SUBJECT_BG)
                pdf.set_font("Helvetica", "B", 8.5)
            else:
                pdf.set_fill_color(*(WHITE if idx % 2 == 0 else SECTION_BG))
                pdf.set_font("Helvetica", "", 8.5)
            pdf.set_draw_color(*HAIRLINE)
            pdf.set_text_color(*DARK_GRAY)
            name = _truncate(c["name"], 44)
            if is_subj:
                name += "  <- YOU"
            pdf.cell(c_rank, 6.6, f"  #{c['rank']}", fill=True, border="B")
            pdf.cell(c_name, 6.6, f"  {name}", fill=True, border="B")
            pdf.cell(c_rating, 6.6, str(c["rating"]), fill=True, border="B", align="C")
            pdf.cell(c_rev, 6.6, str(c["reviews"]), fill=True, border="B", align="C")
            pdf.ln()
        pdf.ln(3)

        # Gap insight — computed straight from the pack. Branches on whether the
        # business actually trails on reviews, so the takeaway is always accurate.
        leader = competitors[0]
        your_rev = subject["reviews"] if subject else reviews
        above = [c for c in competitors if subject and c["rank"] < subject["rank"]]
        if above:
            avg_above = round(statistics.mean(c["reviews"] for c in above))
            if your_rev >= avg_above:
                gap_text = (
                    f"You already hold more reviews ({your_rev}) than the {len(above)} listings ranked "
                    f"above you (avg {avg_above}). That points away from raw review count and toward "
                    f"proximity to the searcher, your primary Google category, review recency, and profile "
                    f"activity as what is moving your rank. A deep-dive audit isolates which lever to pull."
                )
            else:
                gap = max(leader["reviews"] - your_rev, 0)
                gap_text = (
                    f"The {len(above)} listings ranked above you average {avg_above} reviews. The #1 spot, "
                    f"{_truncate(leader['name'], 40)}, holds {leader['reviews']} vs your {your_rev} -- a gap "
                    f"of {gap}. Review volume and recency drive roughly 16% of local-pack position "
                    f"(Whitespark 2025), so closing this gap is your highest-leverage move."
                )
        else:
            gap_text = (
                f"The strongest listing in your pack, {_truncate(leader['name'], 40)}, holds "
                f"{leader['reviews']} reviews vs your {your_rev}. Protect your position by keeping "
                f"review velocity and profile activity ahead of the pack."
            )
        pdf.set_fill_color(*SECTION_BG)
        pdf.set_draw_color(*LIGHT_GRAY)
        gy = pdf.get_y()
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(*DARK_GRAY)
        gl = pdf.multi_cell(CONTENT_W - 6, 5, gap_text, dry_run=True, output="LINES")
        gh = len(gl) * 5 + 4
        pdf.rect(MARGIN, gy, CONTENT_W, gh, "DF")
        pdf.set_xy(MARGIN + 3, gy + 2)
        pdf.multi_cell(CONTENT_W - 6, 5, gap_text, new_x="LMARGIN", new_y="NEXT")
        pdf.set_y(gy + gh + 5)
    else:
        # No pack data on file — still give substance, never a blank region.
        _section_heading(pdf, "What a Full Audit Maps For You")
        _bullets(pdf, [
            "Every competitor in your Local Pack ranked by reviews, rating, and recent movement.",
            "The exact review gap between you and the top 3 -- with a target to close it.",
            "Which Google Business Profile signals the leaders have that you are missing.",
        ], max_items=4)
        pdf.ln(2)

    # Optional trend chart (rich, multi-week leads only — never forces a page)
    rt = insights.get("rank_trend")
    if rt and rt.get("history"):
        chart_path = _trend_chart(rt["history"], rt.get("direction", "stable"))
        if chart_path:
            if pdf.get_y() > 205:
                pdf.add_page()
            _section_heading(pdf, "Your 6-Week Ranking Trend")
            cy = pdf.get_y()
            ch_h = CONTENT_W / 3.3  # figure aspect is 6.6:2.0 -> exact, no overlap
            pdf.image(chart_path, x=MARGIN, y=cy, w=CONTENT_W, h=ch_h)
            pdf.set_y(cy + ch_h + 3)
            trend_desc = {
                "declining": "A sustained decline -- act now to reverse it before it becomes the new normal.",
                "volatile": "Your rank is swinging, a sign Google is testing you against competitors. "
                            "Consistent profile activity stabilises it.",
                "improving": "Trending up -- keep the review and posting cadence that is working.",
                "stable": "Largely stable. Small, consistent gains move you into the top tier.",
            }
            _para(pdf, [trend_desc.get(rt.get("direction", "stable"), "")])
            try:
                Path(chart_path).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass

    # NEXT STEPS / pricing — compact, side by side
    if pdf.get_y() > 225:
        pdf.add_page()
    pdf.ln(1)
    _section_heading(pdf, "Recover Your Ranking")
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*DARK_GRAY)
    pdf.multi_cell(CONTENT_W, 5,
                   "Two ways to act on this report. Reply to this email to start -- no contracts, "
                   "cancel anytime.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    box_y = pdf.get_y()
    bw = (CONTENT_W - 8) / 2
    _service_box(pdf, MARGIN, box_y, bw,
                 "Deep-Dive Audit Report",
                 ["10+ specific fixes ranked by impact",
                  "Full competitor gap analysis",
                  "GBP optimisation checklist"],
                 "$10 one-time", primary=False)
    _service_box(pdf, MARGIN + bw + 8, box_y, bw,
                 "Map Pack Guardian",
                 ["Weekly rank + review tracking",
                  "Instant drop alerts",
                  "Competitor movement intel"],
                 "$5 / month", primary=True)
    pdf.set_y(box_y + 52 + 6)

    # Footer
    pdf.set_draw_color(*HAIRLINE)
    pdf.line(MARGIN, pdf.get_y(), 210 - MARGIN, pdf.get_y())
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*MID_GRAY)
    pdf.cell(CONTENT_W, 4, "Search Sentinel  |  sutraflow.org  |  Automated Local SEO Intelligence",
             align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*LIGHT_GRAY)
    pdf.set_font("Helvetica", "I", 7)
    pdf.cell(CONTENT_W, 4, f"Generated {report_date}. Sources: Google Maps, Whitespark Local Ranking "
             "Factors 2025, BrightLocal 2025.", align="C")

    # ── Save ──────────────────────────────────────────────────────────────────
    safe = "".join(c if c.isalnum() or c in "-_ " else "" for c in biz)
    safe = safe.replace(" ", "-").lower()[:50]
    filename = f"audit_{safe}_{datetime.now().strftime('%Y%m%d')}.pdf"
    filepath = Path(reporter.reports_dir) / filename
    pdf.output(str(filepath))
    logger.info(f"report_pdf: generated {filepath}")
    return filepath
