"""
Send a sample audit PDF to an owner/test address using the real sending path.

Purpose:
  1. Lets you (or anyone) preview the exact PDF a prospect receives.
  2. Runs through agents.email_sender.send_email, so it ALSO live-tests the
     Resend -> Gmail fallback: the run log will show "Resend: sent",
     or "Resend: 429 ... falling back" + "Gmail SMTP: sent".

Recipient resolution: argv[1]  ->  $RECIPIENT  ->  sutrafloworg@gmail.com

Run locally:   python send_sample.py you@example.com
In CI:         set RECIPIENT + secrets, then `python send_sample.py`
No Claude API key needed — the PDF uses data-derived fallbacks + the
Local Pack leaderboard from rankings_history.json.
"""
import json
import os
import sys
import types
from pathlib import Path

import config
from agents.email_sender import active_backend, send_email
from agents.report_pdf import build_report_pdf


def _pick_real_lead() -> dict:
    """Pick a real pending lead whose category has Local Pack history, so the
    competitor leaderboard populates. Falls back to a synthetic alert."""
    try:
        hist = json.loads(Path(config.RANKINGS_FILE).read_text(encoding="utf-8"))
    except Exception:
        hist = {}
    try:
        reports = json.loads(Path(config.PENDING_REPORTS_FILE).read_text(encoding="utf-8")).get("reports", [])
    except Exception:
        reports = []

    for r in reports:
        a = r.get("alert_data", {})
        if a.get("category_key") in hist and a.get("business_name"):
            return a

    # Synthetic fallback (still renders a full, never-blank report)
    return {
        "category_key": "new york_ny_cosmetic-dentist",
        "business_name": "Sample Dental Studio",
        "prev_rank": 3, "curr_rank": 9, "rank_change": 6,
        "rating": 4.8, "reviews": 142, "prev_reviews": 142,
        "reasons": [], "weeks_tracked": 2, "insights": {},
    }


def _label(category_key: str) -> tuple[str, str, str]:
    parts = (category_key or "").split("_")
    city = parts[0].title() if parts else "Your City"
    state = parts[1].upper() if len(parts) > 1 else ""
    category = parts[2].replace("-", " ").title() if len(parts) > 2 else "Local Business"
    return city, state, category


def main() -> int:
    recipient = (sys.argv[1] if len(sys.argv) > 1 else None) or os.environ.get("RECIPIENT") or "sutrafloworg@gmail.com"

    alert = _pick_real_lead()
    city, state, category = _label(alert.get("category_key", ""))
    reporter = types.SimpleNamespace(
        reports_dir=config.REPORTS_DIR,
        rankings_file=config.RANKINGS_FILE,
    )
    pdf_path = build_report_pdf(reporter, alert, "", city, state, category)
    print(f"Built sample PDF: {pdf_path}")
    print(f"Email backend (preferred): {active_backend()}")

    subject = "Sample — your Search Sentinel local ranking audit"
    plain = (
        "Hi,\n\nAttached is a sample of the free local-SEO ranking audit Search Sentinel "
        "sends to businesses that drop in Google Maps rankings.\n\nReply to this email if "
        "you'd like the full deep-dive audit.\n\n-- Search Sentinel | sutraflow.org\n"
    )
    html = (
        "<p>Hi,</p><p>Attached is a sample of the free local-SEO ranking audit "
        "<b>Search Sentinel</b> sends to businesses that drop in Google Maps rankings.</p>"
        "<p>Reply to this email if you'd like the full deep-dive audit.</p>"
        "<p style='color:#666'>-- Search Sentinel | sutraflow.org</p>"
    )

    ok = send_email(
        to_email=recipient,
        subject=subject,
        plain=plain,
        html=html,
        from_name="Search Sentinel",
        pdf_path=Path(pdf_path),
    )
    print(f"Sent: {ok}  ->  {recipient}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
