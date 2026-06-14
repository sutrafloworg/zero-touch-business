"""
Send a sample audit PDF to an owner/test address using the real sending path.

Purpose:
  1. Preview the exact PDF a prospect receives.
  2. Runs through agents.email_sender.send_email, so it ALSO live-tests the
     Resend -> Gmail fallback (the run log shows "Resend: sent", or
     "Resend: 429 ... falling back" + "Gmail SMTP: sent").

Variants (env SAMPLE_VARIANT, or argv[2]):
  typical  - week-2 lead: competitor leaderboard + review-gap (what most prospects get)
  detailed - multi-week lead: adds the 6-week ranking-trend chart + full analyst prose
  both     - sends one email per variant

Recipient: argv[1] -> $RECIPIENT -> sutrafloworg@gmail.com
No Claude API key needed — PDFs use data-derived content + the Local Pack leaderboard.
"""
import json
import os
import sys
import types
from pathlib import Path

import config
from agents.email_sender import active_backend, send_email
from agents.report_pdf import build_report_pdf


def _pack(key: str) -> list:
    """Latest Local Pack results for a category from rankings_history.json."""
    try:
        hist = json.loads(Path(config.RANKINGS_FILE).read_text(encoding="utf-8"))
    except Exception:
        return []
    entry = hist.get(key, {})
    snaps = entry.get("snapshots")
    results = snaps[-1]["results"] if snaps else entry.get("results", [])
    return sorted(results, key=lambda x: x.get("rank", 99))


def _label(category_key: str) -> tuple[str, str, str]:
    parts = (category_key or "").split("_")
    city = parts[0].title() if parts else "Your City"
    state = parts[1].upper() if len(parts) > 1 else ""
    category = parts[2].replace("-", " ").title() if len(parts) > 2 else "Local Business"
    return city, state, category


def _typical() -> tuple:
    """A real week-2 lead whose category has Local Pack history (leaderboard populates)."""
    hist_keys = set()
    try:
        hist_keys = set(json.loads(Path(config.RANKINGS_FILE).read_text(encoding="utf-8")).keys())
    except Exception:
        pass
    try:
        reports = json.loads(Path(config.PENDING_REPORTS_FILE).read_text(encoding="utf-8")).get("reports", [])
    except Exception:
        reports = []
    for r in reports:
        a = r.get("alert_data", {})
        if a.get("category_key") in hist_keys and a.get("business_name"):
            city, state, cat = _label(a["category_key"])
            return a, "", city, state, cat, "Sample (typical) - Search Sentinel local ranking audit"
    alert = {"category_key": "new york_ny_cosmetic-dentist", "business_name": "Sample Dental Studio",
             "prev_rank": 3, "curr_rank": 9, "rank_change": 6, "rating": 4.8, "reviews": 142,
             "prev_reviews": 142, "reasons": [], "weeks_tracked": 2, "insights": {}}
    return alert, "", "New York", "NY", "Cosmetic Dentist", "Sample (typical) - Search Sentinel local ranking audit"


def _detailed() -> tuple:
    """A rich multi-week lead: 6-week declining trend + analyst prose (renders the trend chart)."""
    key = "new york_ny_personal-injury-lawyer"
    pack = _pack(key)
    subj = pack[6] if len(pack) > 6 else (pack[-1] if pack else
            {"name": "Sample Injury Law Firm", "rank": 7, "rating": 4.7, "reviews": 210})
    rank = subj.get("rank", 7)
    name = subj.get("name", "Sample Injury Law Firm")
    reviews = int(subj.get("reviews", 210) or 210)
    alert = {
        "category_key": key, "business_name": name,
        "prev_rank": 3, "curr_rank": rank, "rank_change": max(rank - 3, 3),
        "rating": subj.get("rating", 4.7), "reviews": reviews, "prev_reviews": reviews - 1,
        "reasons": [
            "Competitors above you gained reviews: The Law Office of Richard M. Kenny gained 9 new reviews",
            "No new reviews this week -- Google favors actively reviewed businesses",
            "2 competitors above you have higher ratings",
        ],
        "weeks_tracked": 6,
        "insights": {
            "rank_trend": {"direction": "declining", "history": [3, 3, 4, 5, 6, rank],
                           "best_rank": 3, "worst_rank": rank},
            "review_velocity": {"reviews_per_week": 0.3, "over_weeks": 6, "total_gained": 2, "verdict": "stagnant"},
        },
    }
    audit_text = (
        "WHAT HAPPENED\n"
        f"Our scan recorded {name} sliding from #3 to #{rank} over six weeks. The Law Office of "
        "Richard M. Kenny, now #1, added 9 reviews in the same window while your profile added none.\n\n"
        "WHY\n"
        "- [HIGH CONFIDENCE] A competitor directly above you gained 9 reviews this period; your count was flat.\n"
        "- [MEDIUM CONFIDENCE] Two listings above you carry higher ratings, widening the trust gap.\n"
        "- [LOW CONFIDENCE] Six weeks of steady decline points to a sustained shift, not a one-week blip.\n\n"
        "QUICK WINS\n"
        "Action: Request reviews from your last 15 clients this week. Why now: your velocity is 0.3/wk vs a "
        "3/wk benchmark. Effort: Low. Expected impact: High.\n"
        "Action: Add 3 case-result posts to your Google profile. Why now: active profiles are favored and yours "
        "has been dormant. Effort: Med. Expected impact: Med.\n"
        "Action: Audit your primary category against the #1 listing. Why now: category match is a core ranking "
        "signal. Effort: Low. Expected impact: Med.\n"
    )
    return alert, audit_text, "New York", "NY", "Personal Injury Lawyer", "Sample (detailed) - Search Sentinel local ranking audit"


def main() -> int:
    recipient = (sys.argv[1] if len(sys.argv) > 1 else None) or os.environ.get("RECIPIENT") or "sutrafloworg@gmail.com"
    variant = (os.environ.get("SAMPLE_VARIANT") or (sys.argv[2] if len(sys.argv) > 2 else "") or "typical").strip().lower()
    builders = {"typical": [_typical], "detailed": [_detailed], "both": [_typical, _detailed]}.get(variant, [_typical])

    reporter = types.SimpleNamespace(reports_dir=config.REPORTS_DIR, rankings_file=config.RANKINGS_FILE)
    print(f"Variant: {variant}  | Email backend (preferred): {active_backend()}")
    all_ok = True
    for build in builders:
        alert, audit_text, city, state, category, subject = build()
        pdf_path = build_report_pdf(reporter, alert, audit_text, city, state, category)
        print(f"Built PDF: {pdf_path}")
        plain = ("Hi,\n\nAttached is a sample of the free local-SEO ranking audit Search Sentinel sends to "
                 "businesses that drop in Google Maps rankings.\n\nReply if you'd like the full deep-dive.\n\n"
                 "-- Search Sentinel | sutraflow.org\n")
        html = ("<p>Hi,</p><p>Attached is a sample of the free local-SEO ranking audit <b>Search Sentinel</b> "
                "sends to businesses that drop in Google Maps rankings.</p><p>Reply if you'd like the full "
                "deep-dive.</p><p style='color:#666'>-- Search Sentinel | sutraflow.org</p>")
        ok = send_email(to_email=recipient, subject=subject, plain=plain, html=html,
                        from_name="Search Sentinel", pdf_path=Path(pdf_path))
        print(f"Sent ({subject}): {ok}  ->  {recipient}")
        all_ok = all_ok and ok
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
