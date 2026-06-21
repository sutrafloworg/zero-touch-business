"""
report_pdf — premium, data-rich PDF layout for Search Sentinel audit reports.

Design goals (this is the deliverable customers pay for):
  * Four dense pages, every one full of specific, data-derived content -- never a
    blank or near-blank page.
  * Real charts built from the full Local Pack history in rankings_history.json
    (rank trajectory, review race, category benchmark) with no text overlap.
  * A consultant-grade competitive leaderboard with per-competitor review growth
    and rank movement -- the single most useful, specific thing in the report.
  * A data-derived Google Business Profile checklist and a concrete recovery plan.

All analytics come from agents.report_analytics.compute_analytics, so the heavy
lifting is testable without anthropic/fpdf. This module only lays out the page.

``build_report_pdf`` is a drop-in replacement called by ReportAgent.generate_audit
and by send_sample.py.
"""
from __future__ import annotations

import logging
import tempfile
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

try:
    from agents.report_analytics import compute_analytics, _short
except Exception:  # noqa: BLE001 - allow running as a loose script
    from report_analytics import compute_analytics, _short

logger = logging.getLogger(__name__)

# ── Palette ───────────────────────────────────────────────────────────────────
BLACK = (15, 18, 24)
WHITE = (255, 255, 255)
DARK_GRAY = (44, 50, 60)
MID_GRAY = (108, 116, 128)
LIGHT_GRAY = (188, 196, 206)
HAIRLINE = (228, 232, 238)
ACCENT = (0, 102, 204)
ACCENT_DK = (0, 74, 150)
RED_TEXT = (188, 36, 36)
AMBER_BG = (255, 247, 230)
AMBER_LINE = (217, 145, 20)
AMBER_TEXT = (140, 84, 10)
GREEN_TEXT = (24, 120, 52)
GREEN_BG = (235, 247, 238)
SECTION_BG = (246, 248, 251)
SUBJECT_BG = (255, 244, 224)
TILE_BG = (244, 248, 253)

# Hex equivalents for matplotlib
H_ACCENT = "#0066cc"
H_RED = "#bc2424"
H_GREEN = "#187834"
H_AMBER = "#d99114"
H_GRAY = "#6c7480"
H_DARK = "#2c323c"

MARGIN = 15
PAGE_W = 210
CONTENT_W = PAGE_W - 2 * MARGIN  # 180

_REPLACEMENTS = {
    "—": "--", "–": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
    "…": "...", "•": "-", " ": " ", "→": "->", "←": "<-", "‣": ">",
    "●": "*", "★": "*", "⭐": "*", "™": "(TM)", "®": "(R)", "·": "-",
}


def _san(text) -> str:
    s = str(text)
    for ch, rep in _REPLACEMENTS.items():
        s = s.replace(ch, rep)
    return s.encode("latin-1", errors="replace").decode("latin-1")


def _plural(n: int, word: str = "review") -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _truncate(s: str, n: int) -> str:
    s = _san(s)
    return s if len(s) <= n else s[: max(n - 1, 1)].rstrip() + "."


def _clean_bullet(line: str) -> str:
    line = _san(line).strip()
    for prefix in ("- ", "* ", "> "):
        if line.startswith(prefix):
            line = line[len(prefix):].strip()
            break
    i = 0
    while i < len(line) and line[i].isdigit():
        i += 1
    if i > 0 and i < len(line) and line[i] in ".)":
        line = line[i + 1:].strip()
    if line[:7].lower() == "action:":
        line = line[7:].strip()
    return line


def _parse_sections(audit_text: str) -> dict:
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
    return sections


# ══════════════════════════════════════════════════════════════════════════════
# Charts (matplotlib). Each returns a temp PNG path at a fixed W:H aspect so the
# caller can place it at a known height with zero distortion or overlap.
# ══════════════════════════════════════════════════════════════════════════════
def _new_fig(w_in: float, h_in: float):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(w_in, h_in), dpi=170)
    return plt, fig, ax


def _finish(plt, fig) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    fig.savefig(tmp.name, facecolor="white", bbox_inches=None)
    plt.close(fig)
    return tmp.name


def _chart_rank_trend(ranks: list[int], dates: list[str], direction: str) -> str | None:
    try:
        plt, fig, ax = _new_fig(7.0, 2.25)
        color = {"declining": H_RED, "volatile": H_AMBER,
                 "improving": H_GREEN, "stable": H_ACCENT}.get(direction, H_ACCENT)
        x = list(range(len(ranks)))
        lo, hi = min(ranks), max(ranks)
        pad = max(1, (hi - lo) * 0.25 + 1)
        ax.fill_between(x, ranks, hi + pad, alpha=0.07, color=color, zorder=0)
        # top-3 band
        ax.axhspan(0.5, 3.5, color=H_GREEN, alpha=0.06, zorder=0)
        ax.plot(x, ranks, color=color, linewidth=2.6, marker="o", markersize=8,
                markerfacecolor="white", markeredgewidth=2.4, markeredgecolor=color, zorder=5)
        for xi, rv in zip(x, ranks):
            c = H_GREEN if rv <= 3 else (H_RED if rv >= 7 else H_DARK)
            ax.annotate(f"#{rv}", (xi, rv), textcoords="offset points", xytext=(0, 11),
                        ha="center", fontsize=9, fontweight="bold", color=c, zorder=6)
        ax.invert_yaxis()
        ax.set_ylim(hi + pad, max(0.4, lo - pad * 0.8))
        ax.set_xlim(-0.4, len(ranks) - 0.6)
        ax.set_xticks(x)
        ax.set_xticklabels(dates, fontsize=8.5, color=H_GRAY)
        ax.set_ylabel("Google rank", fontsize=9, color=H_GRAY)
        ax.tick_params(axis="y", labelsize=8.5, colors=H_GRAY)
        # 'Top 3' label pinned top-right inside the band, clear of the line
        ax.text(len(ranks) - 0.62, 2.0, "Top 3 zone", fontsize=7.5, color=H_GREEN,
                ha="right", va="center", style="italic", zorder=4)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color("#d8dce2")
        ax.grid(axis="y", alpha=0.18, linestyle="--", linewidth=0.6)
        fig.subplots_adjust(left=0.085, right=0.985, top=0.93, bottom=0.13)
        return _finish(plt, fig)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"report_pdf: rank trend chart failed: {e}")
        return None


def _chart_review_race(rows: list[tuple[str, int, bool]]) -> str | None:
    """rows: list of (label, review_gain, is_you) already ordered top->bottom."""
    try:
        plt, fig, ax = _new_fig(7.0, 2.45)
        labels = [r[0] for r in rows]
        vals = [r[1] for r in rows]
        you = [r[2] for r in rows]
        y = list(range(len(rows)))[::-1]  # first row on top
        colors = [H_RED if u else "#9aa4b0" for u in you]
        bars = ax.barh(y, vals, color=colors, height=0.62, zorder=3)
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=8.6, color=H_DARK)
        vmax = max(vals + [1])
        ax.set_xlim(0, vmax * 1.16)
        for yi, v, u in zip(y, vals, you):
            ax.text(v + vmax * 0.015, yi, f"+{v}", va="center", ha="left",
                    fontsize=8.6, fontweight="bold" if u else "normal",
                    color=H_RED if u else H_DARK)
        ax.set_xlabel("Reviews added over the tracked window", fontsize=8.4, color=H_GRAY)
        ax.tick_params(axis="x", labelsize=7.5, colors=H_GRAY)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        ax.spines["bottom"].set_color("#d8dce2")
        ax.grid(axis="x", alpha=0.15, linestyle="--", linewidth=0.6)
        fig.subplots_adjust(left=0.30, right=0.97, top=0.96, bottom=0.18)
        return _finish(plt, fig)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"report_pdf: review race chart failed: {e}")
        return None


def _chart_benchmark(your_rev: int, typ_rev: int, top_rev: int,
                     your_rating: float, avg_rating: float) -> str | None:
    try:
        plt, fig, ax = _new_fig(7.0, 1.75)
        import matplotlib.pyplot as plt2  # noqa
        # left axis: reviews (3 bars), right: rating (2 bars) via a second axes
        gs = fig.add_gridspec(1, 2, width_ratios=[1.45, 1], wspace=0.28)
        axL = fig.add_subplot(gs[0, 0])
        axR = fig.add_subplot(gs[0, 1])
        ax.remove()

        # Reviews
        rl = ["You", "Typical\ncompetitor", "Category\nleader"]
        rv = [your_rev, typ_rev, top_rev]
        rc = [H_RED, "#9aa4b0", H_DARK]
        yb = [2, 1, 0]
        axL.barh(yb, rv, color=rc, height=0.6)
        axL.set_yticks(yb); axL.set_yticklabels(rl, fontsize=8, color=H_DARK)
        axL.set_xlim(0, max(rv + [1]) * 1.22)
        for yi, v in zip(yb, rv):
            axL.text(v + max(rv + [1]) * 0.02, yi, f"{v:,}", va="center",
                     ha="left", fontsize=8, fontweight="bold", color=H_DARK)
        axL.set_title("Total reviews", fontsize=8.6, color=H_DARK, loc="left", pad=4)
        for s in ("top", "right", "left"):
            axL.spines[s].set_visible(False)
        axL.spines["bottom"].set_color("#d8dce2")
        axL.tick_params(axis="x", labelsize=7, colors=H_GRAY)
        axL.tick_params(axis="y", length=0)
        axL.grid(axis="x", alpha=0.13, linestyle="--", linewidth=0.6)

        # Rating
        gl = ["You", "Category\navg"]
        gv = [your_rating, avg_rating]
        gc = [H_RED if your_rating < avg_rating else H_GREEN, "#9aa4b0"]
        yb2 = [1, 0]
        axR.barh(yb2, gv, color=gc, height=0.55)
        axR.set_yticks(yb2); axR.set_yticklabels(gl, fontsize=8, color=H_DARK)
        axR.set_xlim(0, 5.4)
        for yi, v in zip(yb2, gv):
            axR.text(v - 0.05, yi, f"{v}", va="center", ha="right",
                     fontsize=8, fontweight="bold", color="white")
        axR.set_title("Star rating", fontsize=8.6, color=H_DARK, loc="left", pad=4)
        for s in ("top", "right", "left"):
            axR.spines[s].set_visible(False)
        axR.spines["bottom"].set_color("#d8dce2")
        axR.tick_params(axis="x", labelsize=7, colors=H_GRAY)
        axR.tick_params(axis="y", length=0)
        axR.set_xticks([0, 1, 2, 3, 4, 5])

        fig.subplots_adjust(left=0.13, right=0.985, top=0.86, bottom=0.06)
        return _finish(plt, fig)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"report_pdf: benchmark chart failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Low-level draw helpers
# ══════════════════════════════════════════════════════════════════════════════
def _page_title(pdf: FPDF, title: str, date: str):
    pdf.set_xy(MARGIN, 13)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*BLACK)
    pdf.cell(CONTENT_W * 0.7, 8, title)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*MID_GRAY)
    pdf.cell(CONTENT_W * 0.3, 8, date, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*ACCENT)
    pdf.set_line_width(0.5)
    pdf.line(MARGIN, pdf.get_y(), PAGE_W - MARGIN, pdf.get_y())
    pdf.set_line_width(0.2)
    pdf.ln(4)


def _heading(pdf: FPDF, title: str):
    y = pdf.get_y()
    pdf.set_fill_color(*ACCENT)
    pdf.rect(MARGIN, y + 0.6, 3, 5.6, "F")
    pdf.set_xy(MARGIN + 6, y)
    pdf.set_font("Helvetica", "B", 10.5)
    pdf.set_text_color(*BLACK)
    pdf.cell(CONTENT_W - 6, 6.6, title.upper(), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.3)


def _metric_strip(pdf: FPDF, y: float, cells: list[tuple], h: float = 18):
    n = len(cells)
    gap = 2.4
    cw = (CONTENT_W - gap * (n - 1)) / n
    for i, (label, value, color) in enumerate(cells):
        cx = MARGIN + i * (cw + gap)
        pdf.set_fill_color(*SECTION_BG)
        pdf.set_draw_color(*HAIRLINE)
        pdf.rect(cx, y, cw, h, "DF")
        pdf.set_xy(cx + 1.8, y + 2.6)
        pdf.set_font("Helvetica", "", 6.7)
        pdf.set_text_color(*MID_GRAY)
        pdf.cell(cw - 3, 3.6, _truncate(label, 18))
        pdf.set_xy(cx + 1.8, y + 7.6)
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*color)
        pdf.cell(cw - 3, 8, _san(value))
    pdf.set_y(y + h)


def _tiles(pdf: FPDF, y: float, tiles: list[tuple], h: float = 20):
    """Big-number tiles with a caption. tiles: (big, caption, color)."""
    n = len(tiles)
    gap = 3.0
    cw = (CONTENT_W - gap * (n - 1)) / n
    for i, (big, caption, color) in enumerate(tiles):
        cx = MARGIN + i * (cw + gap)
        pdf.set_fill_color(*TILE_BG)
        pdf.set_draw_color(*HAIRLINE)
        pdf.rect(cx, y, cw, h, "DF")
        pdf.set_fill_color(*color)
        pdf.rect(cx, y, 2.2, h, "F")
        pdf.set_xy(cx + 5, y + 3.2)
        pdf.set_font("Helvetica", "B", 17)
        pdf.set_text_color(*color)
        pdf.cell(cw - 7, 8, _san(big))
        pdf.set_xy(cx + 5, y + 12)
        pdf.set_font("Helvetica", "", 7.4)
        pdf.set_text_color(*DARK_GRAY)
        pdf.multi_cell(cw - 7, 3.4, _san(caption))
    pdf.set_y(y + h)


def _callout(pdf: FPDF, text: str, kind: str = "amber"):
    bg, line, txt = {
        "amber": (AMBER_BG, AMBER_LINE, AMBER_TEXT),
        "section": (SECTION_BG, LIGHT_GRAY, DARK_GRAY),
        "green": (GREEN_BG, GREEN_TEXT, GREEN_TEXT),
    }[kind]
    y0 = pdf.get_y()
    pdf.set_font("Helvetica", "B" if kind == "amber" else "", 9.2)
    pdf.set_fill_color(*bg)
    pdf.set_draw_color(*line)
    lines = pdf.multi_cell(CONTENT_W - 7, 4.8, _san(text), dry_run=True, output="LINES")
    box_h = len(lines) * 4.8 + 4.2
    pdf.rect(MARGIN, y0, CONTENT_W, box_h, "DF")
    pdf.set_xy(MARGIN + 3.5, y0 + 2)
    pdf.set_text_color(*txt)
    pdf.multi_cell(CONTENT_W - 7, 4.8, _san(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(y0 + box_h + 3.5)


def _bullets(pdf: FPDF, lines: list[str], numbered=False, max_items=4, size=9.6):
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
        pdf.set_xy(MARGIN + 3, y)
        pdf.set_font("Helvetica", "B", size)
        pdf.set_text_color(*ACCENT)
        pdf.cell(6, 5.2, marker)
        pdf.set_font("Helvetica", "", size)
        pdf.set_text_color(*DARK_GRAY)
        pdf.set_xy(MARGIN + 9, y)
        pdf.multi_cell(CONTENT_W - 9, 5.2, _san(text), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1.0)


def _para(pdf: FPDF, text, size=9.8, color=DARK_GRAY):
    if isinstance(text, list):
        text = " ".join(t.strip() for t in text if t and t.strip())
    text = _san(text).strip()
    if not text:
        return
    pdf.set_font("Helvetica", "", size)
    pdf.set_text_color(*color)
    pdf.multi_cell(CONTENT_W, 5.2, text, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.2)


def _check_row(pdf: FPDF, ok: bool, label: str, detail: str):
    y = pdf.get_y()
    mark = "OK" if ok else "X"
    mc = GREEN_TEXT if ok else RED_TEXT
    pdf.set_fill_color(*(GREEN_BG if ok else (255, 244, 244)))
    pdf.rect(MARGIN, y, 7.5, 7.5, "F")
    pdf.set_xy(MARGIN, y + 1.4)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*mc)
    pdf.cell(7.5, 4.6, mark, align="C")
    pdf.set_xy(MARGIN + 10, y)
    pdf.set_font("Helvetica", "B", 9.2)
    pdf.set_text_color(*BLACK)
    pdf.cell(CONTENT_W - 10, 4.4, _truncate(label, 70), new_x="LMARGIN", new_y="NEXT")
    pdf.set_xy(MARGIN + 10, pdf.get_y())
    pdf.set_font("Helvetica", "", 8.4)
    pdf.set_text_color(*MID_GRAY)
    pdf.multi_cell(CONTENT_W - 10, 4.0, _san(detail), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.8)


def _service_box(pdf: FPDF, x, y, bw, title, bullets, price, primary):
    bh = 50
    if primary:
        pdf.set_fill_color(*ACCENT)
        pdf.set_draw_color(*ACCENT)
        pdf.rect(x, y, bw, bh, "D")
        pdf.rect(x, y, bw, 9, "F")
        pdf.set_text_color(*WHITE)
    else:
        pdf.set_fill_color(*SECTION_BG)
        pdf.set_draw_color(*LIGHT_GRAY)
        pdf.rect(x, y, bw, bh, "D")
        pdf.rect(x, y, bw, 9, "F")
        pdf.set_text_color(*BLACK)
    pdf.set_xy(x + 3, y + 1.6)
    pdf.set_font("Helvetica", "B", 9.6)
    pdf.cell(bw - 6, 6, _truncate(title, 34))
    ly = y + 12.5
    for b in bullets:
        pdf.set_xy(x + 3, ly)
        pdf.set_font("Helvetica", "", 8.2)
        pdf.set_text_color(*ACCENT)
        pdf.cell(3, 4.3, "-")
        pdf.set_text_color(*DARK_GRAY)
        pdf.set_xy(x + 6, ly)
        pdf.multi_cell(bw - 9, 4.3, _truncate(b, 62))
        ly = pdf.get_y() + 0.7
    pdf.set_xy(x + 3, y + bh - 9.5)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*ACCENT)
    pdf.cell(bw - 6, 7, price)
    return bh


# ══════════════════════════════════════════════════════════════════════════════
# Main entry point
# ══════════════════════════════════════════════════════════════════════════════
def build_report_pdf(reporter, alert: dict, audit_text: str,
                     city: str, state: str, category: str) -> Path:
    city, state, category = _san(city), _san(state), _san(category)
    biz = _san(alert.get("business_name", "Your Business"))
    report_date = datetime.now().strftime("%B %d, %Y")
    sections = _parse_sections(audit_text)

    # Analytics: prefer one computed upstream (generate_audit), else compute here.
    an = alert.get("_analytics")
    if not an:
        try:
            an = compute_analytics(alert, getattr(reporter, "rankings_file", None))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"report_pdf: analytics failed: {e}")
            an = {}

    # Reconcile current standing with the freshest data available.
    curr = an.get("current_rank") or alert.get("curr_rank", "?")
    start = an.get("start_rank")
    prev = alert.get("prev_rank", "?")
    rating = (an.get("benchmarks") or {}).get("your_rating") or alert.get("rating", "N/A")
    reviews = (an.get("benchmarks") or {}).get("your_reviews")
    if reviews is None:
        reviews = int(alert.get("reviews", 0) or 0)
    weeks = round(an.get("weeks_span") or 0) or an.get("n_snapshots") or alert.get("weeks_tracked", 1)
    best = an.get("best_rank")
    vis = an.get("visibility") or {}
    bench = an.get("benchmarks") or {}
    rt = an.get("rank_trend")
    rv = an.get("review_velocity")
    comps = an.get("competitors") or []
    subject = an.get("subject")
    reasons = [_san(r) for r in alert.get("reasons", [])]

    pdf = FPDF()
    pdf.set_left_margin(MARGIN)
    pdf.set_right_margin(MARGIN)
    pdf.set_auto_page_break(auto=False)

    # ═══════════════ PAGE 1 — DIAGNOSIS ═══════════════════════════════════════
    pdf.add_page()
    pdf.set_fill_color(*BLACK)
    pdf.rect(0, 0, PAGE_W, 38, "F")
    pdf.set_fill_color(*ACCENT)
    pdf.rect(0, 38, PAGE_W, 2.0, "F")
    pdf.set_xy(MARGIN, 8)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*WHITE)
    pdf.cell(120, 10, "Search Sentinel")
    pdf.set_xy(PAGE_W - MARGIN - 60, 10)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(165, 174, 186)
    pdf.cell(60, 5, report_date, align="R")
    pdf.set_xy(PAGE_W - MARGIN - 60, 15)
    pdf.cell(60, 5, "CONFIDENTIAL", align="R")
    pdf.set_xy(MARGIN, 20.5)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(150, 160, 174)
    pdf.cell(150, 6, "Local Search Ranking Audit")

    pdf.set_xy(MARGIN, 45)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*MID_GRAY)
    pdf.cell(CONTENT_W, 4.5, "PREPARED FOR", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(MARGIN)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*BLACK)
    pdf.multi_cell(CONTENT_W, 8, biz, new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(MARGIN)
    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_text_color(*MID_GRAY)
    pdf.cell(CONTENT_W, 6, f"{category}  |  {city}, {state}".strip(" |"),
             new_x="LMARGIN", new_y="NEXT")

    pdf.ln(2.5)
    rank_color = RED_TEXT if isinstance(curr, int) and curr > 3 else GREEN_TEXT
    net = an.get("net_change")
    if isinstance(net, int) and net != 0:
        change_label, change_val = ("Net Change", f"{net:+d}")
        change_color = GREEN_TEXT if net > 0 else RED_TEXT
    else:
        change_label, change_val, change_color = ("Best Rank", f"#{best}" if best else f"#{prev}", DARK_GRAY)
    _metric_strip(pdf, pdf.get_y(), [
        ("Current Rank", f"#{curr}", rank_color),
        ("Started At", f"#{start}" if start else f"#{prev}", DARK_GRAY),
        (change_label, change_val, change_color),
        ("Rating", str(rating), DARK_GRAY),
        ("Reviews", f"{reviews:,}", DARK_GRAY),
        ("Weeks Tracked", str(weeks), ACCENT),
    ])

    pdf.ln(3)
    if isinstance(curr, int) and curr > 3:
        rel = vis.get("lost_vs_best_rel", 0)
        loss_clause = (f" Since your best position of #{best}, that is roughly {rel}% fewer "
                       f"clicks reaching you." if best and best < curr and rel else "")
        impact = (f"At #{curr} you sit below the top 3 -- where ~{vis.get('top3_ctr_pct', 70)}% of "
                  f"Google Maps clicks land. Your position now captures an estimated "
                  f"{vis.get('your_ctr_pct', 3)}% of clicks, about {vis.get('reach_vs_top3_x', '5')}x "
                  f"fewer searchers than a top-3 listing.{loss_clause}")
    elif isinstance(curr, int):
        impact = (f"At #{curr} you are inside the top 3, capturing an estimated "
                  f"{vis.get('your_ctr_pct', 20)}% of Google Maps clicks. Top-3 spots turn over "
                  f"fast -- this report shows who is closing in and how to hold the position.")
    else:
        impact = "Your ranking moved against you this period. The detail below shows where and why."
    _callout(pdf, impact, "amber")

    # Visibility tiles
    _heading(pdf, "What Your Position Is Costing You")
    reachx = vis.get("reach_vs_top3_x") or "-"
    _tiles(pdf, pdf.get_y(), [
        (f"{vis.get('your_ctr_pct', '-')}%", "estimated share of Map clicks you capture now", ACCENT),
        (f"{vis.get('top3_ctr_pct', '-')}%", "share the top 3 listings split between them", GREEN_TEXT),
        (f"{reachx}x", "fewer searchers reach you vs a top-3 rival", RED_TEXT),
    ])
    pdf.ln(3)

    _heading(pdf, "What Happened")
    wh = sections.get("what happened")
    if not wh or not any(s.strip() for s in wh):
        traj = ""
        if rt and len(rt["history"]) >= 3:
            traj = (f" Over {round(an.get('weeks_span') or weeks)} weeks of tracking your rank moved "
                    f"{' -> '.join('#'+str(r) for r in rt['history'])}.")
        wh = [f"Our {report_date} scan places {biz} at position #{curr} for \"{category}\" searches "
              f"in {city}, {state}." + (f" You started the tracked window at #{start}." if start else "") + traj]
    _para(pdf, wh)

    _heading(pdf, "Why You Slipped")
    why = sections.get("why")
    if not why or not any(s.strip() for s in why):
        why = []
        # A climber only pressures your spot if it sits at or above your rank.
        threats = sorted([m for m in an.get("movement", [])
                          if m.get("rank_delta", 0) > 0 and isinstance(curr, int) and m["rank"] <= curr],
                         key=lambda m: (-m["rank_delta"], -m.get("review_gain", 0)))
        if threats:
            t = threats[0]
            why.append(f"[HIGH CONFIDENCE] {_short(t['name'])} climbed +{t['rank_delta']} positions to "
                       f"#{t['rank']} while adding {_plural(t.get('review_gain', 0))} -- direct pressure on your spot.")
        if bench and bench.get("your_reviews", 0) < bench.get("typical_reviews", 0):
            gap = bench["typical_reviews"] - bench["your_reviews"]
            why.append(f"[{'HIGH' if not threats else 'MEDIUM'} CONFIDENCE] Your {bench['your_reviews']:,} reviews "
                       f"trail the typical competitor's {bench['typical_reviews']:,} (a {gap:,} gap), weakening a "
                       f"core ranking signal.")
        if rv and rv["per_week"] < 1:
            why.append(f"[MEDIUM CONFIDENCE] Your review velocity is {rv['per_week']}/week ({rv['verdict']}); "
                       f"listings adding 3+/week tend to climb past slower profiles.")
        fg = an.get("fastest_review_gainer")
        if not threats and fg and fg.get("review_gain", 0) > 0:
            why.append(f"[MEDIUM CONFIDENCE] {_short(fg['name'])} added {_plural(fg['review_gain'])} this window "
                       f"while your profile added {_plural((rv or {}).get('total_gained', 0))} -- momentum is shifting.")
        if not why:
            why = reasons or ["[LOW CONFIDENCE] A multi-position move in a short window is more often a "
                              "competitor's review burst or profile change than a penalty on your listing."]
    _bullets(pdf, why, numbered=False, max_items=4)

    pdf.ln(1.5)
    _heading(pdf, "Your Standing")
    standing = sections.get("your standing")
    if standing and any(t.strip() for t in standing):
        _para(pdf, standing, size=9.4)
    else:
        conf = alert.get("_confidence_score")
        if rt:
            dirn = rt["direction"]
            verdict = {"declining": "a real, sustained decline worth acting on now",
                       "volatile": "an unstable position Google is still testing -- consistency will settle it",
                       "improving": "positive momentum worth protecting",
                       "stable": "a steady hold that small, consistent gains can convert into a climb"}.get(dirn, "")
            base = (f"Across {round(an.get('weeks_span') or weeks)} weeks and {an.get('n_snapshots', weeks)} "
                    f"scans, the data reads as {verdict}.")
        else:
            base = ("This is an early read from your most recent scan; the picture sharpens as weekly "
                    "tracking accumulates.")
        if conf:
            base += f" Overall data-confidence for this audit: {conf}/10."
        _para(pdf, base, size=9.4)

    _footer_note(pdf, "Data source: Google Maps Local Pack, collected via automated weekly scans. "
                      "Rankings vary by device and location.")

    # ═══════════════ PAGE 2 — PERFORMANCE INTELLIGENCE ════════════════════════
    pdf.add_page()
    _page_title(pdf, "Performance Intelligence", report_date)

    drew_any = False
    if rt and len(rt["history"]) >= 2:
        _heading(pdf, f"Your Ranking Trend ({round(an.get('weeks_span') or weeks)} weeks)")
        cp = _chart_rank_trend(rt["history"], rt["dates"], rt["direction"])
        if cp:
            y = pdf.get_y()
            h = CONTENT_W / (7.0 / 2.25)
            pdf.image(cp, x=MARGIN, y=y, w=CONTENT_W, h=h)
            pdf.set_y(y + h + 2)
            Path(cp).unlink(missing_ok=True)
            drew_any = True
        desc = {"declining": "A sustained decline -- the recovery actions in this report are aimed at reversing it.",
                "volatile": "Your rank is swinging week to week, a sign Google is re-testing you against rivals. "
                            "Consistent profile activity stabilises it.",
                "improving": "Trending up -- keep the review and posting cadence that is working.",
                "stable": "Holding steady. Small, consistent gains are what move you into the top tier."}
        _para(pdf, desc.get(rt["direction"], ""), size=9.4)
        pdf.ln(1)

    # Review race
    movement = {m["_key"]: m for m in an.get("movement", [])}
    race_rows = []
    if subject and an.get("movement"):
        your_gain = movement.get(subject["_key"], {}).get("review_gain", 0)
        race_rows.append(("You (" + _truncate(biz, 22) + ")", max(your_gain, 0), True))
        others = sorted(an["movement"], key=lambda m: -m.get("review_gain", 0))
        for m in others:
            if subject and m["_key"] == subject["_key"]:
                continue
            if m.get("review_gain", 0) <= 0:
                continue
            race_rows.append((_truncate(m["name"], 26), m["review_gain"], False))
            if len(race_rows) >= 6:
                break
    if len(race_rows) >= 2:
        _heading(pdf, "The Review Race -- Who's Pulling Ahead")
        cp = _chart_review_race(race_rows)
        if cp:
            y = pdf.get_y()
            h = CONTENT_W / (7.0 / 2.45)
            pdf.image(cp, x=MARGIN, y=y, w=CONTENT_W, h=h)
            pdf.set_y(y + h + 2)
            Path(cp).unlink(missing_ok=True)
            drew_any = True
        lead_gain = race_rows[0][1] if race_rows[0][2] else max(r[1] for r in race_rows)
        top_other = max((r[1] for r in race_rows if not r[2]), default=0)
        msg = ("Reviews and their recency drive roughly 16% of local-pack position (Whitespark 2025). "
               + (f"Competitors are adding them faster than you -- the top rival gained {top_other} in this window."
                  if top_other > (race_rows[0][1] if race_rows[0][2] else 0) else
                  "Keep your review velocity ahead of the pack to defend your position."))
        _para(pdf, msg, size=9.4)
        pdf.ln(1)

    # Benchmark
    if bench:
        _heading(pdf, "How You Compare in Your Category")
        cp = _chart_benchmark(bench["your_reviews"], bench["typical_reviews"], bench["top_reviews"],
                              bench["your_rating"], bench["avg_rating"])
        if cp:
            y = pdf.get_y()
            h = CONTENT_W / (7.0 / 1.75)
            pdf.image(cp, x=MARGIN, y=y, w=CONTENT_W, h=h)
            pdf.set_y(y + h + 2)
            Path(cp).unlink(missing_ok=True)
            drew_any = True
        pctl = bench["reviews_percentile"]
        stand = ("ahead of most of your market" if pctl >= 60 else
                 "in the middle of your market" if pctl >= 35 else "behind most of your market")
        _para(pdf, f"By review volume you are in the {pctl}th percentile of your {bench['pack_size']}-listing "
                   f"pack -- {stand}. The category leader holds {bench['top_reviews']:,} reviews; the typical "
                   f"competitor holds {bench['typical_reviews']:,}.", size=9.4)

    if not drew_any:
        _callout(pdf, "Your market is newly tracked, so multi-week charts will populate as scans accumulate. "
                      "The competitive leaderboard on the next page is built from your most recent live scan.",
                 "section")

    _footer_note(pdf, "Benchmarks computed from the live Local Pack for your city and category. "
                      "Sources: Whitespark 2025, BrightLocal 2025.")

    # ═══════════════ PAGE 3 — COMPETITIVE LANDSCAPE ═══════════════════════════
    pdf.add_page()
    _page_title(pdf, "Competitive Landscape", report_date)

    if comps:
        _heading(pdf, "Who's Winning Your Market")
        c_rank, c_rating, c_rev, c_gain = 13, 22, 24, 24
        c_name = CONTENT_W - c_rank - c_rating - c_rev - c_gain
        pdf.set_fill_color(*BLACK)
        pdf.set_text_color(*WHITE)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(c_rank, 7.2, "  #", fill=True)
        pdf.cell(c_name, 7.2, "  Business", fill=True)
        pdf.cell(c_rating, 7.2, "Rating", fill=True, align="C")
        pdf.cell(c_rev, 7.2, "Reviews", fill=True, align="C")
        pdf.cell(c_gain, 7.2, "Rev +/-", fill=True, align="C")
        pdf.ln()

        rows = comps[:8]
        if subject and subject not in rows:
            rows = comps[:7] + [subject]
        for idx, c in enumerate(rows):
            is_subj = subject is not None and c is subject
            mv = movement.get(c["_key"], {})
            gain = mv.get("review_gain")
            if is_subj:
                pdf.set_fill_color(*SUBJECT_BG)
                pdf.set_font("Helvetica", "B", 8.2)
            else:
                pdf.set_fill_color(*(WHITE if idx % 2 == 0 else SECTION_BG))
                pdf.set_font("Helvetica", "", 8.2)
            pdf.set_draw_color(*HAIRLINE)
            pdf.set_text_color(*DARK_GRAY)
            name = _truncate(c["name"], 40)
            if is_subj:
                name += "  <- YOU"
            pdf.cell(c_rank, 6.4, f"  #{c['rank']}", fill=True, border="B")
            pdf.cell(c_name, 6.4, f"  {name}", fill=True, border="B")
            pdf.cell(c_rating, 6.4, str(c["rating"]), fill=True, border="B", align="C")
            pdf.cell(c_rev, 6.4, f"{c['reviews']:,}", fill=True, border="B", align="C")
            gtxt = "--" if gain is None else (f"+{gain}" if gain > 0 else str(gain))
            if gain and gain > 0:
                pdf.set_text_color(*GREEN_TEXT)
            pdf.cell(c_gain, 6.4, gtxt, fill=True, border="B", align="C")
            pdf.set_text_color(*DARK_GRAY)
            pdf.ln()
        pdf.ln(2.5)

        # Gap insight
        leader = comps[0]
        your_rev = subject["reviews"] if subject else reviews
        above = [c for c in comps if subject and c["rank"] < subject["rank"]]
        if above:
            import statistics as _st
            avg_above = round(_st.mean(c["reviews"] for c in above))
            if your_rev >= avg_above:
                gap_text = (f"You already hold more reviews ({your_rev:,}) than the {_plural(len(above), 'listing')} ranked "
                            f"above you (avg {avg_above:,}). That points away from raw review count and toward "
                            f"proximity, your primary Google category, review recency, and profile activity as "
                            f"what is moving your rank. The recovery plan targets exactly those.")
            else:
                gap = max(leader["reviews"] - your_rev, 0)
                gap_text = (f"The {_plural(len(above), 'listing')} above you average {avg_above:,} reviews. The #1 spot, "
                            f"{_truncate(leader['name'], 34)}, holds {leader['reviews']:,} vs your {your_rev:,} "
                            f"-- a gap of {gap:,}. Closing it is your single highest-leverage move.")
        else:
            gap_text = (f"You lead your pack. The nearest challenger, {_truncate(comps[min(1,len(comps)-1)]['name'],34)}, "
                        f"is the one to watch -- keep review velocity and profile activity ahead of them.")
        _callout(pdf, gap_text, "section")

        # Movers
        _heading(pdf, "Who's Moving")
        fc = an.get("fastest_climber")
        fg = an.get("fastest_review_gainer")
        fallers = sorted([m for m in an.get("movement", []) if m.get("rank_delta", 0) < 0],
                         key=lambda m: m["rank_delta"])
        mv_lines = []
        used = set()
        if fc and fc.get("rank_delta", 0) > 0:
            mv_lines.append(f"Fastest climber: {_short(fc['name'])} rose +{fc['rank_delta']} positions to "
                            f"#{fc['rank']} (+{_plural(fc.get('review_gain', 0))}).")
            used.add(fc["_key"])
        if fg and fg["_key"] not in used and fg.get("review_gain", 0) > 0:
            mv_lines.append(f"Most reviews added: {_short(fg['name'])} +{_plural(fg['review_gain'])} (now {fg['reviews']:,} total).")
            used.add(fg["_key"])
        for f0 in fallers:
            if f0["_key"] in used:
                continue
            mv_lines.append(f"Biggest drop: {_short(f0['name'])} fell {abs(f0['rank_delta'])} positions to #{f0['rank']} "
                            f"-- an opening if you act before they recover.")
            used.add(f0["_key"])
            break
        if not mv_lines:
            mv_lines = ["Your pack has been stable this window. Stability is an opportunity: a steady push on "
                        "reviews and profile activity can move you up while rivals hold still."]
        _bullets(pdf, mv_lines, numbered=False, max_items=4, size=9.4)

        if bench:
            pdf.ln(1.5)
            _heading(pdf, "Market Snapshot")
            share = (an.get("visibility") or {}).get("share_pct")
            _tiles(pdf, pdf.get_y(), [
                (str(bench["pack_size"]), "listings competing in this Local Pack", ACCENT),
                (str(bench["avg_rating"]), "average star rating across the pack", DARK_GRAY),
                (f"{bench['typical_reviews']:,}", "reviews held by the typical competitor", DARK_GRAY),
                (f"{share}%" if share is not None else "--", "of pack click-share you hold today", GREEN_TEXT if (share or 0) >= 15 else RED_TEXT),
            ], h=19)
    else:
        _heading(pdf, "What a Full Audit Maps For You")
        _bullets(pdf, [
            "Every competitor in your Local Pack ranked by reviews, rating, and recent movement.",
            "The exact review gap between you and the top 3 -- with a target to close it.",
            "Which Google Business Profile signals the leaders have that you are missing.",
        ], max_items=4)

    _footer_note(pdf, "Competitor data reflects your most recent live Local Pack scan. "
                      "Movement is measured across the full tracked window.")

    # ═══════════════ PAGE 4 — RECOVERY PLAN ═══════════════════════════════════
    pdf.add_page()
    _page_title(pdf, "Your Recovery Plan", report_date)

    _heading(pdf, "Do This Next")
    quick = sections.get("quick wins")
    if not quick or not any(s.strip() for s in quick):
        quick = []
        if rv and rv["per_week"] < 3:
            quick.append("Ask your last 15 happy customers for a Google review this week. Why now: your "
                         f"velocity is {rv['per_week']}/week against a 3/week benchmark. Effort: Low. Impact: High.")
        else:
            quick.append("Keep your review request routine running weekly -- it is the habit holding your rank. "
                         "Effort: Low. Impact: High.")
        quick.append("Make your Google Business Profile categories, hours, and service area exactly match your "
                     "website. Why now: mismatches quietly suppress ranking. Effort: Low. Impact: Med.")
        quick.append("Post a weekly Google update (offer, photo, or job done). Why now: active profiles outrank "
                     "dormant ones. Effort: Med. Impact: Med.")
    _bullets(pdf, quick, numbered=True, max_items=4, size=9.6)
    pdf.ln(1)

    gbp = an.get("gbp") or []
    if gbp:
        _heading(pdf, "Your Google Business Profile Checklist")
        for item in gbp[:5]:
            _check_row(pdf, item["ok"], item["label"], item["detail"])
        pdf.ln(1)

    # Pricing
    if pdf.get_y() > 232:
        pdf.set_y(232)
    _heading(pdf, "Recover Your Ranking")
    pdf.set_font("Helvetica", "", 9.4)
    pdf.set_text_color(*DARK_GRAY)
    pdf.multi_cell(CONTENT_W, 5, "Two ways to act on this report. Reply to this email to start -- no contracts, "
                                 "cancel anytime.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.5)
    by = pdf.get_y()
    bw = (CONTENT_W - 8) / 2
    _service_box(pdf, MARGIN, by, bw, "Deep-Dive Audit Report",
                 ["10+ specific fixes ranked by impact", "Full competitor gap analysis",
                  "GBP optimisation checklist"], "$10 one-time", primary=False)
    _service_box(pdf, MARGIN + bw + 8, by, bw, "Map Pack Guardian",
                 ["Weekly rank + review tracking", "Instant drop alerts",
                  "Competitor movement intel"], "$5 / month", primary=True)
    pdf.set_y(by + 50 + 5)
    pdf.set_draw_color(*HAIRLINE)
    pdf.line(MARGIN, pdf.get_y(), PAGE_W - MARGIN, pdf.get_y())
    pdf.ln(2.5)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*MID_GRAY)
    pdf.cell(CONTENT_W, 4, "Search Sentinel  |  sutraflow.org  |  Automated Local SEO Intelligence",
             align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(*LIGHT_GRAY)
    pdf.cell(CONTENT_W, 4, f"Generated {report_date}. Sources: Google Maps, Whitespark & BrightLocal 2025 "
                          "local ranking studies.", align="C")

    # ── Save ──────────────────────────────────────────────────────────────────
    safe = "".join(c if c.isalnum() or c in "-_ " else "" for c in biz).replace(" ", "-").lower()[:50]
    filename = f"audit_{safe}_{datetime.now().strftime('%Y%m%d')}.pdf"
    filepath = Path(reporter.reports_dir) / filename
    pdf.output(str(filepath))
    logger.info(f"report_pdf: generated {filepath}")
    return filepath


def _footer_note(pdf: FPDF, text: str):
    pdf.set_y(-12)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(*LIGHT_GRAY)
    pdf.cell(CONTENT_W, 4, _san(text), align="C")
