"""
Testimonial Outreach — sends free PDF audit reports to select contacts
and asks them to leave a testimonial in exchange.

Strategy:
  1. Pick N contacts with the most dramatic rank drops (most compelling data)
  2. Generate their full PDF report for free (skip Stripe gate)
  3. Send the PDF with a personal note asking for a testimonial
  4. Track who was sent a free report in pending_reports.json

Usage:
  python3 testimonial_outreach.py --dry-run        # preview who would get it
  python3 testimonial_outreach.py --count 10       # send to top 10 contacts
  python3 testimonial_outreach.py                  # send to top 5 (default)

Requirements:
  - ANTHROPIC_API_KEY, GMAIL_USER, GMAIL_APP_PASSWORD must be set
  - pending_reports.json must have qualifying contacts
"""
import argparse
import json
import logging
import os
import smtplib
import time
from datetime import datetime, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import config
from agents.outreach_agent import OutreachAgent, is_valid_outreach_email
from agents.json_store import atomic_write_json, safe_load_json
from agents import suppression

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
PENDING_FILE = DATA_DIR / "pending_reports.json"
CUSTOMERS_FILE = DATA_DIR / "customers.json"
TESTIMONIAL_PAGE = "https://sutraflow.org/sentinel/#testimonial"  # update when page exists


def _load_json(path: Path, default):
    return safe_load_json(path, default)


def _save_pending(data: dict):
    atomic_write_json(PENDING_FILE, data)


def _score_report(report: dict) -> int:
    """Score a report by how compelling the data is (higher = better testimonial candidate)."""
    alert = report.get("alert_data", {})
    score = 0
    # Big rank drop = more dramatic story
    score += alert.get("rank_change", 0) * 10
    # More reasons = more specific insights in report
    score += len(alert.get("reasons", [])) * 5
    # Higher weeks tracked = more progressive insights
    score += report.get("alert_data", {}).get("weeks_tracked", 1) * 2
    # Penalize if already got a free report
    if report.get("free_report_sent"):
        score = -999
    return score


def _send_free_report_email(
    gmail_user: str,
    gmail_password: str,
    to_email: str,
    business_name: str,
    pdf_path: Path,
    alert: dict,
) -> bool:
    """Send the free PDF with a testimonial ask."""
    category_parts = alert.get("category_key", "").split("_")
    city = category_parts[0].title() if category_parts else "your city"
    prev_rank = alert.get("prev_rank", "?")
    curr_rank = alert.get("curr_rank", "?")
    rank_change = alert.get("rank_change", 1)

    subject = f"Free ranking report for {business_name} — no strings attached"

    html_body = f"""
<div style="font-family:-apple-system,sans-serif;max-width:560px;margin:0 auto;color:#1a1a1a;line-height:1.6">
  <p>Hi,</p>

  <p>My name is Samik — I run <strong>Search Sentinel</strong>, a tool that monitors
  Google Maps rankings for local businesses.</p>

  <p>A few weeks ago I noticed that <strong>{business_name}</strong> dropped from
  #{prev_rank} to #{curr_rank} in {city} ({rank_change} position{'s' if rank_change != 1 else ''}).
  I put together a full audit report — and I'm attaching it for free.</p>

  <p><strong>No catch.</strong> The report is yours to keep. It covers:</p>
  <ul>
    <li>Which competitors passed you and why</li>
    <li>The 2–3 most likely causes of the drop</li>
    <li>Specific actions to recover your position (all DIY, no agency needed)</li>
  </ul>

  <div style="background:#f0fdf4;border-left:4px solid #16a34a;padding:14px 18px;margin:20px 0;border-radius:0 6px 6px 0">
    <p style="margin:0;font-size:14px;color:#166534">
      <strong>One small ask:</strong> if you find the report useful, I'd love a quick
      2-sentence testimonial I can share on our website. Just reply to this email —
      no form, no login, just a sentence or two about whether it was helpful.
    </p>
  </div>

  <p>Either way, I hope the report gives you something useful to act on.</p>

  <p>Best,<br>
  Samik<br>
  <span style="color:#777;font-size:13px">Search Sentinel · samik.sarkar@columbia.edu</span>
  </p>

  <hr style="border:none;border-top:1px solid #e5e5e5;margin:20px 0">
  <p style="font-size:11px;color:#999">
    Search Sentinel · Hillsborough, NJ 08844 · To unsubscribe, reply "unsubscribe"
  </p>
</div>""".strip()

    plain_body = (
        f"Hi,\n\nMy name is Samik — I run Search Sentinel, a tool that monitors "
        f"Google Maps rankings for local businesses.\n\n"
        f"A few weeks ago I noticed {business_name} dropped from #{prev_rank} to #{curr_rank} "
        f"in {city}. I put together a full audit report — attaching it for free.\n\n"
        f"No catch. The report covers which competitors passed you and why, the most likely "
        f"causes, and specific actions to recover your position.\n\n"
        f"One small ask: if you find the report useful, I'd love a quick 2-sentence testimonial "
        f"I can share on our website. Just reply to this email — no form, no login needed.\n\n"
        f"Hope it's useful,\nSamik\nSearch Sentinel\n\n"
        f"--\nTo unsubscribe, reply 'unsubscribe'."
    )

    try:
        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = f"Samik at Search Sentinel <{gmail_user}>"
        msg["To"] = to_email
        msg["Reply-To"] = gmail_user

        # Text parts
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(plain_body, "plain"))
        alt.attach(MIMEText(html_body, "html"))
        msg.attach(alt)

        # PDF attachment
        if pdf_path and pdf_path.exists():
            with open(pdf_path, "rb") as f:
                pdf_data = f.read()
            attachment = MIMEBase("application", "pdf")
            attachment.set_payload(pdf_data)
            encoders.encode_base64(attachment)
            safe_name = business_name.replace("/", "-").replace(" ", "_")[:40]
            attachment.add_header(
                "Content-Disposition", "attachment",
                filename=f"SearchSentinel_Report_{safe_name}.pdf"
            )
            msg.attach(attachment)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, [to_email], msg.as_string())

        logger.info(f"Free report sent to {to_email} ({business_name})")
        return True

    except Exception as e:
        logger.error(f"Failed to send free report to {to_email}: {e}")
        return False


def run_testimonial_outreach(count: int = 5, dry_run: bool = False) -> dict:
    """
    Select top-N contacts by drama score, generate free PDF, send with testimonial ask.
    """
    gmail_user = config.GMAIL_USER
    gmail_password = config.GMAIL_APP_PASSWORD

    if not gmail_user or not gmail_password:
        logger.error("Gmail credentials not configured")
        return {}

    pending_data = _load_json(PENDING_FILE, {"reports": []})
    customers_data = _load_json(CUSTOMERS_FILE, {"customers": []})
    paying_emails = {
        c["email"].lower().strip()
        for c in customers_data.get("customers", [])
        if c.get("status") == "active"
    }

    # Filter eligible reports
    eligible = []
    seen_emails = set()
    for report in pending_data["reports"]:
        email = report.get("email", "")
        if not is_valid_outreach_email(email):
            continue
        if suppression.is_suppressed(email):
            continue  # unsubscribed or hard-bounced
        if email.lower().strip() in paying_emails:
            continue  # already a customer
        if report.get("free_report_sent"):
            continue  # already got free report
        if email.lower().strip() in seen_emails:
            continue  # dedupe by email
        seen_emails.add(email.lower().strip())
        eligible.append(report)

    # Sort by drama score
    eligible.sort(key=_score_report, reverse=True)
    selected = eligible[:count]

    logger.info(f"Eligible contacts: {len(eligible)}, selected top {len(selected)}")

    if not selected:
        logger.info("No eligible contacts found")
        return {"sent": 0, "skipped": 0}

    # Initialize report agent for PDF generation
    from agents.report_agent import ReportAgent
    reporter = ReportAgent(
        api_key=config.ANTHROPIC_API_KEY,
        reports_dir=config.REPORTS_DIR,
        model=config.CLAUDE_MODEL,
    )

    stats = {"sent": 0, "skipped": 0, "failed": 0}
    changed = False

    for report in selected:
        email = report["email"]
        business_name = report["business_name"]
        alert = report.get("alert_data", {})

        if dry_run:
            score = _score_report(report)
            logger.info(
                f"[DRY RUN] Would send free report to {email} ({business_name}) "
                f"rank {alert.get('prev_rank','?')}→{alert.get('curr_rank','?')} "
                f"score={score}"
            )
            stats["sent"] += 1
            continue

        logger.info(f"Generating PDF for {business_name} ({email})...")
        try:
            pdf_path = reporter.generate_audit(alert)
            pdf_path = Path(pdf_path) if pdf_path else None
        except Exception as e:
            logger.error(f"PDF generation failed for {business_name}: {e}")
            stats["failed"] += 1
            continue

        success = _send_free_report_email(
            gmail_user, gmail_password,
            email, business_name, pdf_path, alert
        )

        if success:
            report["free_report_sent"] = datetime.now(timezone.utc).isoformat()
            report["free_report_email"] = email
            stats["sent"] += 1
            changed = True
            time.sleep(8)  # rate limit between sends
        else:
            stats["failed"] += 1

    if changed:
        _save_pending(pending_data)
        logger.info("pending_reports.json updated with free report tracking")

    logger.info(f"Testimonial outreach complete: {stats}")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send free reports to get testimonials")
    parser.add_argument("--dry-run", action="store_true", help="Preview without sending")
    parser.add_argument("--count", type=int, default=5, help="Number of contacts to reach (default 5)")
    args = parser.parse_args()

    if args.dry_run:
        logger.info("=== DRY RUN — no emails or PDFs will be generated ===")

    results = run_testimonial_outreach(count=args.count, dry_run=args.dry_run)
    print(f"\nResults: {results}")
