"""
Follow-up Email Sequence — re-engages contacts from pending_reports.json.

Sends up to 3 follow-up emails to anyone who received an initial teaser
but hasn't converted yet:

  Day 3  (followup_1): "Just checking in" — different angle, lower ask
  Day 7  (followup_2): "One specific thing" — concrete tip, builds trust
  Day 14 (followup_3): "Last note" — final soft close, close the loop

Each pending_reports entry gains a new field: followup_history = [
  {"type": "followup_1", "sent_at": "..."},
  {"type": "followup_2", "sent_at": "..."},
  ...
]

Run this from GitHub Actions on a schedule, or manually:
  python3 followup_sequence.py

Only contacts whose initial email was sent >= N days ago AND who haven't
received that follow-up yet will receive a follow-up.

Skips anyone already in customers.json (they paid — no need to follow up).
"""
import json
import logging
import os
import smtplib
import time
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import config
from agents.outreach_agent import is_valid_outreach_email
from agents.json_store import atomic_write_json, safe_load_json
from agents import suppression

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
PENDING_FILE = DATA_DIR / "pending_reports.json"
CUSTOMERS_FILE = DATA_DIR / "customers.json"

FOLLOWUP_DELAYS = {
    "followup_1": 3,   # days after initial send
    "followup_2": 7,
    "followup_3": 14,
}


def _load_json(path: Path, default):
    return safe_load_json(path, default)


def _save_pending(data: dict):
    atomic_write_json(PENDING_FILE, data)


def _active_customer_emails(customers_data: dict) -> set:
    return {
        c["email"].lower().strip()
        for c in customers_data.get("customers", [])
        if c.get("status") == "active"
    }


def _send_followup(
    gmail_user: str,
    gmail_password: str,
    to_email: str,
    business_name: str,
    alert: dict,
    followup_type: str,
    payment_url: str,
    payment_url_audit: str,
    days_since: float = 3.0,
) -> bool:
    category_parts = alert["category_key"].split("_")
    city = category_parts[0].title() if category_parts else "your city"
    category_label = category_parts[2].replace("-", " ") if len(category_parts) > 2 else "your category"
    rank_change = alert.get("rank_change", 1)
    prev_rank = alert.get("prev_rank", "?")
    curr_rank = alert.get("curr_rank", "?")

    if followup_type == "followup_1":
        subject = f"Re: {business_name} — wanted to make sure this reached you"
        days_ago_phrase = (
            "a few days ago" if days_since < 10
            else f"about {int(days_since)} days ago" if days_since < 30
            else "a few weeks ago"
        )
        html_body = f"""
<div style="font-family:-apple-system,sans-serif;max-width:560px;margin:0 auto;color:#1a1a1a;line-height:1.6">
  <p>Hi,</p>

  <p>I sent a note {days_ago_phrase} about <strong>{business_name}</strong>'s Google Maps ranking
  dropping from #{prev_rank} to #{curr_rank} in {city} — just wanted to make sure it didn't
  get buried.</p>

  <p>If you're not the right person for this, no worries — feel free to forward it or just ignore.</p>

  <p>If you <em>are</em> the right person: the audit report covers exactly which competitor
  passed you and the 2-3 things most likely to get your ranking back within 30 days.</p>

  <div style="background:#f8f9fa;border:1px solid #e0e0e0;border-radius:6px;padding:14px 18px;margin:18px 0">
    <p style="margin:0 0 10px;font-weight:600;font-size:14px">Get your report:</p>
    <p style="margin:0 0 6px;font-size:14px">
      <a href="{payment_url_audit}" style="color:#0066cc;font-weight:600">Full Audit — $10</a>
      <span style="color:#777;font-size:13px"> · one-time, emailed within minutes</span>
    </p>
    <p style="margin:0;font-size:14px">
      <a href="{payment_url}" style="color:#0066cc;font-weight:600">Weekly Monitoring — $5/mo</a>
      <span style="color:#777;font-size:13px"> · launch price, normally $20/mo</span>
    </p>
  </div>

  <p style="font-size:13px;color:#777">Or just reply and ask me anything.</p>

  <p>Best,<br>Search Sentinel</p>

  <hr style="border:none;border-top:1px solid #e5e5e5;margin:20px 0">
  <p style="font-size:11px;color:#999">
    Search Sentinel · Hillsborough, NJ 08844 · To unsubscribe, reply "unsubscribe"
  </p>
</div>""".strip()

        plain_body = (
            f"Hi,\n\nI sent a note {days_ago_phrase} about {business_name}'s Google Maps ranking "
            f"dropping from #{prev_rank} to #{curr_rank} in {city} — just wanted to make sure it "
            f"didn't get buried.\n\n"
            f"The audit report covers exactly which competitor passed you and what to do about it.\n\n"
            f"Full Audit ($10): {payment_url_audit}\n"
            f"Weekly Monitoring — $5/mo launch price (normally $20/mo): {payment_url}\n\n"
            f"Or just reply with any questions.\n\nBest,\nSearch Sentinel\n\n"
            f"--\nTo unsubscribe, reply 'unsubscribe'."
        )

    elif followup_type == "followup_2":
        subject = f"One quick thing about {business_name}'s Google Maps ranking"
        html_body = f"""
<div style="font-family:-apple-system,sans-serif;max-width:560px;margin:0 auto;color:#1a1a1a;line-height:1.6">
  <p>Hi,</p>

  <p>One thing I've seen help businesses recover Google Maps rankings quickly: <strong>making sure
  your Google Business Profile category matches what you actually rank for.</strong></p>

  <p>For example, if you're listed as a general category but your competitors use a more specific one,
  Google may weight them higher — even if your reviews and engagement are better.</p>

  <p>This is one of the items in the audit report I prepared for <strong>{business_name}</strong>
  (which dropped {rank_change} position{'s' if rank_change != 1 else ''} recently in {city}).</p>

  <p>The full report covers 3 specific actions like this — all things you can do yourself,
  this week, without any SEO agency.</p>

  <div style="background:#eff6ff;border-left:4px solid #2563eb;padding:12px 16px;margin:18px 0;border-radius:0 6px 6px 0">
    <p style="margin:0;font-size:14px">
      <strong><a href="{payment_url_audit}" style="color:#1d4ed8">Get the full report for $10 →</a></strong><br>
      <span style="color:#555;font-size:13px">Emailed automatically within minutes of payment.</span>
    </p>
  </div>

  <p style="font-size:13px;color:#777">Also available: weekly monitoring at $5/mo (launch offer, normally $20/mo) — so you're always the first to know if something shifts.</p>

  <p>Best,<br>Search Sentinel</p>

  <hr style="border:none;border-top:1px solid #e5e5e5;margin:20px 0">
  <p style="font-size:11px;color:#999">
    Search Sentinel · Hillsborough, NJ 08844 · To unsubscribe, reply "unsubscribe"
  </p>
</div>""".strip()

        plain_body = (
            f"Hi,\n\nOne quick tip: making sure your Google Business Profile category matches what "
            f"you actually rank for can have a big impact on Google Maps visibility.\n\n"
            f"This is one of 3 specific items in the audit I put together for {business_name} "
            f"(which dropped {rank_change} spot{'s' if rank_change != 1 else ''} recently in {city}).\n\n"
            f"Get the full report for $10: {payment_url_audit}\n"
            f"(Emailed automatically within minutes of payment.)\n\n"
            f"Also: weekly monitoring at $5/mo — launch price, normally $20/mo: {payment_url}\n\n"
            f"Best,\nSearch Sentinel\n\n"
            f"--\nTo unsubscribe, reply 'unsubscribe'."
        )

    elif followup_type == "followup_3":
        subject = f"One free finding for {business_name} before I close this out"
        html_body = f"""
<div style="font-family:-apple-system,sans-serif;max-width:560px;margin:0 auto;color:#1a1a1a;line-height:1.6">
  <p>Hi,</p>

  <p>This is my last note about <strong>{business_name}</strong>'s Google Maps ranking in {city}.
  Before I close the file, I wanted to share one finding from the audit — no charge.</p>

  <div style="background:#eff6ff;border-left:4px solid #2563eb;padding:12px 16px;margin:18px 0;border-radius:0 6px 6px 0">
    <p style="margin:0 0 6px;font-size:14px;font-weight:600;color:#1d4ed8">Free finding:</p>
    <p style="margin:0;font-size:14px;color:#1e3a5f">
      Businesses that dropped in the <em>{category_label}</em> category in {city} this period
      typically lost ground in one of two areas: review recency (no new reviews in 30+ days)
      or Google Business Profile completeness. Both are fixable in under an hour — no agency needed.
    </p>
  </div>

  <p>The full report has {business_name}'s specific scores on both, plus the ranked list of
  what to fix first. It's $10 and delivered within minutes of payment.</p>

  <div style="background:#f8f9fa;border:1px solid #e0e0e0;border-radius:6px;padding:14px 18px;margin:18px 0">
    <p style="margin:0 0 8px;font-size:14px">
      <a href="{payment_url_audit}" style="color:#0066cc;font-weight:600">Full Audit Report — $10</a>
      <span style="color:#777;font-size:12px"> · instant delivery · money-back guarantee</span>
    </p>
    <p style="margin:0;font-size:14px">
      <a href="{payment_url}" style="color:#0066cc;font-weight:600">Weekly Monitoring — $5/mo</a>
      <span style="color:#999;font-size:12px"> (launch price, normally $20/mo)</span>
    </p>
  </div>

  <p style="font-size:13px;color:#777">
    After this email I won't follow up again — but the report link stays active if you ever want it.
    And if you just want to ask a quick question about your ranking, feel free to reply anytime.
  </p>

  <p>Best,<br>Search Sentinel</p>

  <hr style="border:none;border-top:1px solid #e5e5e5;margin:20px 0">
  <p style="font-size:11px;color:#999">
    Search Sentinel · Hillsborough, NJ 08844 · To unsubscribe, reply "unsubscribe"
  </p>
</div>""".strip()

        plain_body = (
            f"Hi,\n\nThis is my last note about {business_name}'s Google Maps ranking in {city}.\n\n"
            f"Before I close the file, here's one free finding:\n\n"
            f"Businesses that dropped in the {category_label} category in {city} recently typically "
            f"lost ground in review recency (no new reviews in 30+ days) or Google Business Profile "
            f"completeness. Both are fixable in under an hour.\n\n"
            f"The full report has {business_name}'s specific scores on both. $10, instant delivery.\n\n"
            f"Full Audit ($10, money-back guarantee): {payment_url_audit}\n"
            f"Weekly Monitoring ($5/mo launch price): {payment_url}\n\n"
            f"After this I won't follow up again — but feel free to reply anytime if you have questions.\n\n"
            f"Best,\nSearch Sentinel\n\n"
            f"--\nTo unsubscribe, reply 'unsubscribe'."
        )
    else:
        logger.error(f"Unknown followup type: {followup_type}")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Search Sentinel <{gmail_user}>"
        msg["To"] = to_email
        msg["Reply-To"] = gmail_user
        # Gmail 2024 bulk sender compliance headers — RFC 8058 one-click
        unsub_url = f"https://api.sutraflow.org/unsubscribe?email={to_email}"
        msg["List-Unsubscribe"] = (
            f"<{unsub_url}>, <mailto:{gmail_user}?subject=unsubscribe>"
        )
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        msg["Precedence"] = "bulk"
        msg.attach(MIMEText(plain_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, [to_email], msg.as_string())

        logger.info(f"Follow-up {followup_type} sent to {to_email} ({business_name})")
        return True
    except Exception as e:
        logger.error(f"Follow-up {followup_type} failed for {to_email}: {e}")
        return False


def run_followup_sequence(dry_run: bool = False) -> dict:
    """
    Scan pending_reports and send follow-ups where due.
    Returns summary stats.
    """
    gmail_user = config.GMAIL_USER
    gmail_password = config.GMAIL_APP_PASSWORD
    payment_url = config.PAYMENT_URL_MONITORING
    payment_url_audit = config.PAYMENT_URL_AUDIT

    if not gmail_user or not gmail_password:
        logger.error("Gmail credentials not configured — cannot send follow-ups")
        return {}

    pending_data = _load_json(PENDING_FILE, {"reports": []})
    customers_data = _load_json(CUSTOMERS_FILE, {"customers": []})
    active_emails = _active_customer_emails(customers_data)

    now = datetime.now(timezone.utc)
    stats = {"followup_1": 0, "followup_2": 0, "followup_3": 0, "skipped": 0, "already_customer": 0}
    changed = False

    for report in pending_data["reports"]:
        email = report.get("email", "")

        # Skip bad emails
        if not is_valid_outreach_email(email):
            stats["skipped"] += 1
            continue

        # Skip suppressed emails (unsubscribed, completed 3-followup sequence, bounced, etc.)
        if suppression.is_suppressed(email):
            stats["skipped"] += 1
            continue

        # Skip paying customers
        if email.lower().strip() in active_emails:
            stats["already_customer"] += 1
            continue

        created_at_str = report.get("created_at", "")
        if not created_at_str:
            continue
        try:
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        except ValueError:
            continue

        days_since = (now - created_at).total_seconds() / 86400
        followup_history = report.get("followup_history", [])
        sent_types = {fh["type"] for fh in followup_history}

        for fup_type, min_days in FOLLOWUP_DELAYS.items():
            if fup_type in sent_types:
                continue  # already sent this follow-up
            if days_since < min_days:
                continue  # too early

            # Send it
            if dry_run:
                logger.info(f"[DRY RUN] Would send {fup_type} to {email} ({report['business_name']}, {days_since:.0f}d since initial)")
                stats[fup_type] += 1
                break

            success = _send_followup(
                gmail_user, gmail_password,
                email, report["business_name"],
                report["alert_data"],
                fup_type,
                payment_url, payment_url_audit,
                days_since=days_since,
            )
            if success:
                if "followup_history" not in report:
                    report["followup_history"] = []
                report["followup_history"].append({
                    "type": fup_type,
                    "sent_at": now.isoformat(),
                })
                stats[fup_type] += 1
                changed = True
                # Auto-suppress after the final follow-up — they had 3 emails, no pay.
                # Re-contacting them next week if they drop again wastes sender rep.
                if fup_type == "followup_3":
                    suppression.suppress(email, reason="followup_3_completed_no_response")
                time.sleep(5)  # rate limit
            break  # only one follow-up per contact per run

    if changed and not dry_run:
        _save_pending(pending_data)
        logger.info("pending_reports.json updated with follow-up history")

    logger.info(f"Follow-up run complete: {stats}")
    return stats


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    if dry:
        logger.info("=== DRY RUN MODE — no emails will be sent ===")
    results = run_followup_sequence(dry_run=dry)
    print(f"\nResults: {results}")
