"""
report_analytics — turns raw Local Pack history into a rich, report-ready
analytics bundle for a single business.

Why this module exists
----------------------
The audit pipeline had all the data it needed sitting unused in
``data/rankings_history.json`` (7 weekly snapshots per market, each with the
full 20-result Local Pack), yet the per-lead ``insights`` dict was almost
always empty. As a result the AI prompt got thin data, the confidence score
stayed low, and the PDF had no real charts.

``compute_analytics(alert, rankings_file)`` reconstructs, for the subject
business, everything a paid local-SEO audit should contain — entirely from
data already on disk, so it works for *every* lead, even at week 2:

  * real rank history across all snapshots (not just last-vs-prev)
  * review-count history and weekly review velocity (the subject)
  * each competitor's review velocity over the same window
  * category benchmarks (avg/median rating & reviews) and the subject's
    percentile within the pack
  * who climbed / who fell since tracking began (competitive movement)
  * an estimated search-visibility / lost-lead figure from a Local Pack CTR curve
  * a data-derived Google Business Profile checklist (website, hours,
    category match, review gap, rating gap, velocity gap)

The module is dependency-light (stdlib only) so it can be imported anywhere
without pulling in anthropic / matplotlib / fpdf.
"""
from __future__ import annotations

import json
import logging
import re
import statistics
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Local Pack click-through-rate curve. Position 1-3 (the "map pack" shown by
# default) absorb the overwhelming majority of clicks. Values are a blend of
# Whitespark 2025 + BrightLocal 2025 local-pack CTR studies, normalised so the
# top 3 ~ 90% of clicks (the figure used elsewhere in the product copy).
_CTR_CURVE = {
    1: 0.331, 2: 0.221, 3: 0.151, 4: 0.064, 5: 0.048,
    6: 0.034, 7: 0.025, 8: 0.019, 9: 0.015, 10: 0.012,
}
_CTR_TAIL = 0.008  # ranks 11-20


def _ctr(rank) -> float:
    try:
        rank = int(rank)
    except (TypeError, ValueError):
        return 0.0
    return _CTR_CURVE.get(rank, _CTR_TAIL if rank <= 20 else 0.003)


def _norm_name(s: str) -> str:
    """Loose normalisation for matching a business across snapshots."""
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    # drop common suffixes that drift between scrapes
    s = re.sub(r"\b(llc|llp|inc|pllc|pc|pa|co|ltd|the)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _parse_date(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except Exception:  # noqa: BLE001
            return None


def _to_int(v, default=0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _to_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _load_snapshots(alert: dict, rankings_file) -> list[dict]:
    """Return this market's snapshots sorted oldest->newest, each {date, results}."""
    if not rankings_file:
        return []
    try:
        data = json.loads(Path(rankings_file).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"report_analytics: cannot read rankings file: {e}")
        return []
    entry = data.get(alert.get("category_key", ""), {})
    snaps = entry.get("snapshots")
    if not snaps:
        # legacy single-snapshot shape
        res = entry.get("results")
        if res:
            snaps = [{"date": entry.get("last_scan", ""), "results": res}]
        else:
            return []
    out = []
    for s in snaps:
        d = _parse_date(s.get("date") or s.get("scan_date") or "")
        out.append({"dt": d, "date": s.get("date", ""), "results": s.get("results", [])})
    out.sort(key=lambda x: (x["dt"] is None, x["dt"] or datetime.min))
    return out


def _find_in_snapshot(results: list[dict], target_norm: str, place_id: str | None):
    for r in results:
        if place_id and r.get("place_id") and r["place_id"] == place_id:
            return r
    for r in results:
        if _norm_name(r.get("name", "")) == target_norm:
            return r
    # loose containment fallback
    for r in results:
        n = _norm_name(r.get("name", ""))
        if target_norm and (target_norm in n or n in target_norm):
            return r
    return None


def _velocity(history: list[tuple[datetime, int]]) -> float:
    """reviews per week from (date, reviews) points; 0 if <2 points or no span."""
    pts = [(d, v) for d, v in history if d is not None and v is not None]
    if len(pts) < 2:
        return 0.0
    pts.sort(key=lambda x: x[0])
    span_days = (pts[-1][0] - pts[0][0]).days
    if span_days <= 0:
        return 0.0
    return round((pts[-1][1] - pts[0][1]) / (span_days / 7.0), 2)


def compute_analytics(alert: dict, rankings_file) -> dict:
    """Build the full analytics bundle. Always returns a dict; degrades gracefully."""
    biz = alert.get("business_name", "")
    target_norm = _norm_name(biz)
    place_id = alert.get("place_id")
    snaps = _load_snapshots(alert, rankings_file)

    out: dict = {
        "has_history": False,
        "weeks_span": 0.0,
        "n_snapshots": len(snaps),
        "rank_history": [],
        "review_history": [],
        "rank_trend": None,
        "review_velocity": None,
        "competitors": [],
        "subject": None,
        "benchmarks": None,
        "movement": [],
        "fastest_climber": None,
        "fastest_review_gainer": None,
        "visibility": None,
        "gbp": [],
    }

    # ── Subject trajectory across snapshots ──────────────────────────────────
    rank_hist, rev_hist, rating_hist, dates = [], [], [], []
    subject_latest = None
    for s in snaps:
        row = _find_in_snapshot(s["results"], target_norm, place_id)
        if row is not None:
            subject_latest = row
            dates.append(s["dt"])
            rank_hist.append(_to_int(row.get("rank", 99), 99))
            rev_hist.append(_to_int(row.get("reviews", 0)))
            rating_hist.append(_to_float(row.get("rating", 0)))

    latest = snaps[-1] if snaps else None
    latest_results = latest["results"] if latest else []

    # span in weeks
    valid_dates = [d for d in dates if d is not None]
    if len(valid_dates) >= 2:
        out["weeks_span"] = round((max(valid_dates) - min(valid_dates)).days / 7.0, 1)

    out["rank_history"] = [
        {"date": d.strftime("%b %d") if d else "", "rank": r}
        for d, r in zip(dates, rank_hist)
    ]
    out["review_history"] = [
        {"date": d.strftime("%b %d") if d else "", "reviews": rv}
        for d, rv in zip(dates, rev_hist)
    ]

    # ── Rank trend ───────────────────────────────────────────────────────────
    if len(rank_hist) >= 2:
        first, last = rank_hist[0], rank_hist[-1]
        spread = max(rank_hist) - min(rank_hist)
        # rank numbers: smaller is better
        if last - first >= 2:
            direction = "declining"
        elif first - last >= 2:
            direction = "improving"
        elif spread >= 4:
            direction = "volatile"
        else:
            direction = "stable"
        out["rank_trend"] = {
            "history": rank_hist,
            "dates": [d.strftime("%b %d") if d else f"Wk{i+1}" for i, d in enumerate(dates)],
            "direction": direction,
            "best_rank": min(rank_hist),
            "worst_rank": max(rank_hist),
        }
        out["has_history"] = True

    # Current standing = most recent snapshot (more truthful than a stale alert).
    if rank_hist:
        out["current_rank"] = rank_hist[-1]
        out["start_rank"] = rank_hist[0]
        out["net_change"] = rank_hist[0] - rank_hist[-1]  # +ve = improved over window
        out["best_rank"] = min(rank_hist)
        out["worst_rank"] = max(rank_hist)

    # ── Subject review velocity ──────────────────────────────────────────────
    subj_vel = _velocity(list(zip(dates, rev_hist)))
    if len(rev_hist) >= 2:
        total_gained = rev_hist[-1] - rev_hist[0]
        if subj_vel >= 3:
            verdict = "strong"
        elif subj_vel >= 1:
            verdict = "moderate"
        elif subj_vel > 0:
            verdict = "slow"
        else:
            verdict = "stalled"
        out["review_velocity"] = {
            "per_week": subj_vel,
            "total_gained": total_gained,
            "weeks": out["weeks_span"],
            "verdict": verdict,
        }

    # ── Competitors: normalise latest pack + per-competitor velocity ─────────
    # Build a per-place review history map across snapshots for velocity.
    def _key(r):
        return r.get("place_id") or _norm_name(r.get("name", ""))

    rev_series: dict[str, list[tuple[datetime, int]]] = {}
    for s in snaps:
        for r in s["results"]:
            rev_series.setdefault(_key(r), []).append((s["dt"], _to_int(r.get("reviews", 0))))

    comps = []
    for r in latest_results:
        k = _key(r)
        vel = _velocity(rev_series.get(k, []))
        comps.append({
            "rank": _to_int(r.get("rank", 99), 99),
            "name": r.get("name", ""),
            "rating": _to_float(r.get("rating", 0)),
            "reviews": _to_int(r.get("reviews", 0)),
            "review_velocity": vel,
            "website": bool(r.get("website")),
            "type": r.get("type", ""),
            "_key": k,
        })
    comps.sort(key=lambda x: x["rank"])
    out["competitors"] = comps

    # subject row (prefer the matched latest row)
    subject = None
    if subject_latest is not None:
        sk = _key(subject_latest)
        for c in comps:
            if c["_key"] == sk:
                subject = c
                break
    if subject is None:
        cr = alert.get("curr_rank")
        for c in comps:
            if c["rank"] == cr:
                subject = c
                break
    out["subject"] = subject

    # ── Benchmarks (latest pack) ─────────────────────────────────────────────
    if comps:
        ratings = [c["rating"] for c in comps if c["rating"] > 0]
        reviews = [c["reviews"] for c in comps]
        avg_rating = round(statistics.mean(ratings), 2) if ratings else 0
        avg_reviews = round(statistics.mean(reviews)) if reviews else 0
        med_reviews = round(statistics.median(reviews)) if reviews else 0
        your_rev = subject["reviews"] if subject else _to_int(alert.get("reviews", 0))
        your_rating = subject["rating"] if subject else _to_float(alert.get("rating", 0))
        # percentile = share of pack you are at or above
        rev_pct = round(100 * sum(1 for x in reviews if your_rev >= x) / len(reviews)) if reviews else 0
        skewed = bool(avg_reviews and med_reviews and avg_reviews > med_reviews * 1.8)
        out["benchmarks"] = {
            "avg_rating": avg_rating,
            "avg_reviews": avg_reviews,
            "median_reviews": med_reviews,
            "typical_reviews": med_reviews if skewed else avg_reviews,
            "skewed": skewed,
            "your_reviews": your_rev,
            "your_rating": your_rating,
            "reviews_percentile": rev_pct,
            "pack_size": len(comps),
            "top_reviews": max(reviews) if reviews else 0,
        }

    # ── Competitive movement (rank change since first tracked snapshot) ──────
    if len(snaps) >= 2:
        first_results = snaps[0]["results"]
        first_rank = {_key(r): _to_int(r.get("rank", 99), 99) for r in first_results}
        first_rev = {_key(r): _to_int(r.get("reviews", 0)) for r in first_results}
        movers = []
        for c in comps:
            fr = first_rank.get(c["_key"])
            if fr is None:
                continue
            delta = fr - c["rank"]  # positive = climbed
            rev_gain = c["reviews"] - first_rev.get(c["_key"], c["reviews"])
            movers.append({**c, "rank_delta": delta, "review_gain": rev_gain})
        climbers = [m for m in movers if m["rank_delta"] > 0]
        climbers.sort(key=lambda x: (-x["rank_delta"], -x["review_gain"]))
        out["movement"] = movers
        if climbers:
            out["fastest_climber"] = climbers[0]
        gainers = sorted(movers, key=lambda x: -x["review_gain"])
        out["fastest_review_gainer"] = gainers[0] if gainers and gainers[0]["review_gain"] > 0 else None

    # ── Visibility / lost-lead estimate ──────────────────────────────────────
    cr = out.get("current_rank") or (subject["rank"] if subject else alert.get("curr_rank"))
    best = out.get("best_rank", cr)
    your_ctr = _ctr(cr)
    best_ctr = _ctr(best)
    top3_ctr = sum(_ctr(i) for i in (1, 2, 3))
    total_pool = sum(_ctr(c["rank"]) for c in comps) or 1.0
    lost = max(best_ctr - your_ctr, 0)
    out["visibility"] = {
        "current_rank": cr,
        "best_rank": best,
        "your_ctr_pct": round(your_ctr * 100, 1),
        "best_ctr_pct": round(best_ctr * 100, 1),
        "top3_ctr_pct": round(top3_ctr * 100, 1),
        "share_pct": round(100 * your_ctr / total_pool, 1),
        "lost_vs_best_pct": round(lost * 100, 1),
        "lost_vs_best_rel": round(100 * lost / best_ctr) if best_ctr > 0 else 0,
        "reach_vs_top3_x": round(top3_ctr / your_ctr, 1) if your_ctr > 0 else None,
    }

    # ── GBP / profile checklist (data-derived) ───────────────────────────────
    gbp = []
    b = out["benchmarks"]
    if subject is not None:
        has_site = subject.get("website")
        gbp.append({"ok": bool(has_site),
                    "label": "Website linked on Google Business Profile",
                    "detail": "Linked." if has_site else "No website detected on your listing -- add one; it is a ranking and trust signal."})
    if b:
        rev_gap = b["typical_reviews"] - b["your_reviews"]
        gbp.append({"ok": rev_gap <= 0,
                    "label": "Review volume at/above category average",
                    "detail": (f"You have {b['your_reviews']} vs a typical competitor's {b['typical_reviews']}."
                               + ("" if rev_gap <= 0 else f" Close the {rev_gap}-review gap to match the pack."))})
        rating_gap = round(b["avg_rating"] - b["your_rating"], 2)
        gbp.append({"ok": rating_gap <= 0,
                    "label": "Star rating at/above category average",
                    "detail": (f"Your {b['your_rating']} vs category {b['avg_rating']}."
                               + ("" if rating_gap <= 0 else f" A {rating_gap}-star gap costs trust at a glance."))})
    if out["review_velocity"]:
        rv = out["review_velocity"]
        ok = rv["per_week"] >= 1
        gbp.append({"ok": ok,
                    "label": "Adding >=1 review/week (recency signal)",
                    "detail": (f"You are at {rv['per_week']}/week ({rv['verdict']})."
                               + ("" if ok else " Listings that add 3+/week consistently outrank those below 1/week."))})
    if out["fastest_review_gainer"]:
        g = out["fastest_review_gainer"]
        gbp.append({"ok": False,
                    "label": "Keeping pace with the fastest-gaining competitor",
                    "detail": f"{_short(g['name'])} added {g['review_gain']} reviews over the tracked window."})
    out["gbp"] = gbp

    return out


def _short(name: str, n: int = 38) -> str:
    name = name or ""
    return name if len(name) <= n else name[: n - 1].rstrip() + "."


def summarize_for_prompt(an: dict) -> str:
    """Compact, factual block injected into the AI audit prompt. Numbers only."""
    if not an:
        return ""
    L = []
    rt = an.get("rank_trend")
    if rt:
        L.append(f"RANK TREND (measured, {len(rt['history'])} scans over {an['weeks_span']} wks): "
                 f"{rt['direction']}; positions {', '.join('#'+str(r) for r in rt['history'])}; "
                 f"best #{rt['best_rank']}, worst #{rt['worst_rank']}.")
    rv = an.get("review_velocity")
    if rv:
        L.append(f"YOUR REVIEW VELOCITY (measured): {rv['per_week']}/week ({rv['verdict']}), "
                 f"{rv['total_gained']:+d} reviews over {rv['weeks']} wks.")
    b = an.get("benchmarks")
    if b:
        L.append(f"CATEGORY BENCHMARK (measured, {b['pack_size']} listings): your {b['your_reviews']} reviews / "
                 f"{b['your_rating']} stars vs the typical competitor's {b['typical_reviews']} reviews / "
                 f"{b['avg_rating']} stars; you are in the {b['reviews_percentile']}th percentile by review volume; "
                 f"the category leader has {b['top_reviews']}.")
    fc = an.get("fastest_climber")
    if fc and fc.get("rank_delta", 0) > 0:
        L.append(f"FASTEST-CLIMBING COMPETITOR (measured): {_short(fc['name'])} climbed +{fc['rank_delta']} "
                 f"positions to #{fc['rank']}, +{fc.get('review_gain',0)} reviews, {fc['rating']} stars.")
    fg = an.get("fastest_review_gainer")
    if fg:
        L.append(f"FASTEST REVIEW GAINER (measured): {_short(fg['name'])} +{fg['review_gain']} reviews "
                 f"(now {fg['reviews']} total, rank #{fg['rank']}).")
    vis = an.get("visibility")
    if vis:
        L.append(f"VISIBILITY (modeled from local-pack CTR): rank captures ~{vis['your_ctr_pct']}% of clicks; "
                 f"top-3 capture ~{vis['top3_ctr_pct']}%; you reach ~{vis['reach_vs_top3_x']}x fewer searchers "
                 f"than a top-3 listing.")
    return "\n".join(L)
