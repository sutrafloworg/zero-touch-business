"""
Outreach Agent — sends teaser rank-drop emails to businesses.

The full PDF audit report is NOT attached to the initial email.
It is delivered only after payment via the fulfillment agent.

Flow:
  1. Initial outreach: teaser email with rank drop details + Stripe payment links
  2. After payment: fulfillment_agent.py delivers the full PDF

Extracts contact info from:
  1. Google Business Profile data (phone, website from SerpAPI)
  2. Business website (scrapes for email addresses)

CAN-SPAM compliant:
  - Identifies as commercial message
  - Includes physical address
  - Includes opt-out mechanism
  - Honest subject line
"""
import json
import logging
import re
import smtplib
import time
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# Email addresses to never contact
BLOCKLIST_PATTERNS = [
    "noreply@", "no-reply@", "info@example", "test@",
    "admin@", "webmaster@", "support@",
]

# Fake "emails" that are actually image/asset filenames (e.g. cropped-Favicon@512px-32x32.png)
FAKE_EMAIL_TLDS = {
    "png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp",
    "css", "js", "json", "xml", "txt", "pdf", "zip", "woff", "woff2",
}


class OutreachAgent:
    def __init__(
        self,
        gmail_user: str,
        gmail_app_password: str,
        from_name: str = "Search Sentinel",
        max_emails_per_run: int = 10,
        payment_url: str = "",
        payment_url_audit: str = "",
    ):
        self.gmail_user = gmail_user
        self.gmail_app_password = gmail_app_password
        self.from_name = from_name
        self.max_emails_per_run = max_emails_per_run
        self.payment_url = payment_url or "mailto:" + gmail_user
        self.payment_url_audit = payment_url_audit or self.payment_url

    def _insight_bullets(self, alert: dict) -> str:
        """Generate extra email bullet points based on progressive insights."""
        insights = alert.get("insights", {})
        bullets = []

        if "review_velocity" in insights:
            rv = insights["review_velocity"]
            if rv["verdict"] == "stagnant":
                bullets.append("    <li>Your review growth has stalled — competitors are pulling ahead</li>")
            elif rv["verdict"] == "strong":
                bullets.append(f"    <li>Good news: you're averaging {rv['reviews_per_week']} new reviews/week</li>")

        if "rank_trend" in insights:
            rt = insights["rank_trend"]
            if rt["direction"] == "declining":
                bullets.append(f"    <li>Your ranking trend is declining (best: #{rt['best_rank']}, worst: #{rt['worst_rank']})</li>")
            elif rt["direction"] == "volatile":
                bullets.append(f"    <li>Your ranking has been volatile — swinging between #{rt['best_rank']} and #{rt['worst_rank']}</li>")

        if "competitor_spotlight" in insights:
            cs = insights["competitor_spotlight"]
            bullets.append(f"    <li>Watch out: <strong>{cs['fastest_climber']}</strong> climbed {cs['climbed_positions']} spots recently</li>")

        if "category_health" in insights:
            ch = insights["category_health"]
            bullets.append(f"    <li>Your market health score: <strong>{ch['score']}/10</strong> ({ch['position_summary']})</li>")

        return "\n".join(bullets)

    def find_email_from_website(self, website_url: str) -> str | None:
        """Scrape a business website for a contact email address."""
        if not website_url:
            return None

        # Normalize URL
        if not website_url.startswith("http"):
            website_url = f"https://{website_url}"

        try:
            resp = requests.get(
                website_url,
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0 (compatible; SearchSentinelBot/1.0)"},
                allow_redirects=True,
            )
            resp.raise_for_status()

            # Find emails in page content
            emails = re.findall(
                r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
                resp.text,
            )

            # Also check common contact pages
            if not emails:
                for path in ["/contact", "/contact-us", "/about"]:
                    try:
                        contact_resp = requests.get(
                            f"{website_url.rstrip('/')}{path}",
                            timeout=8,
                            headers={"User-Agent": "Mozilla/5.0 (compatible; SearchSentinelBot/1.0)"},
                        )
                        if contact_resp.status_code == 200:
                            emails += re.findall(
                                r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
                                contact_resp.text,
                            )
                    except Exception:
                        continue

            # Filter out blocklisted patterns and asset filenames (e.g. image@512px.png)
            valid_emails = [
                e for e in emails
                if not any(p in e.lower() for p in BLOCKLIST_PATTERNS)
                and e.rsplit(".", 1)[-1].lower() not in FAKE_EMAIL_TLDS
                and "@" in e
                and len(e.split("@")[0]) >= 2
            ]

            if valid_emails:
                email = valid_emails[0]
                logger.info(f"Found email {email} on {website_url}")
                return email

        except Exception as e:
            logger.debug(f"Could not scrape {website_url}: {e}")

        return None

    def send_teaser_email(
        self,
        to_email: str,
        business_name: str,
        alert: dict,
    ) -> bool:
        """Send teaser rank-drop email WITHOUT the full PDF. CAN-SPAM compliant.

        The email highlights the rank drop, shows key insights as a preview,
        and includes Stripe payment links to purchase the full audit or
        subscribe to weekly monitoring.
        """
        if not all([self.gmail_user, self.gmail_app_password, to_email]):
            return False

        category_parts = alert["category_key"].split("_")
        city = category_parts[0].title() if category_parts else "your city"
        category_label = category_parts[2].replace("-", " ") if len(category_parts) > 2 else "your category"

        subject = f"{business_name}: your Google ranking dropped this week"

        insight_bullets = self._insight_bullets(alert)

        # Build reasons preview (show first 2 reasons as teaser)
        reasons = alert.get("reasons", [])
        reasons_html = ""
        if reasons:
            reasons_items = "\n".join(f"    <li>{r}</li>" for r in reasons[:2])
            reasons_html = f"""
  <p style="font-weight:600;margin:16px 0 6px">What's causing the drop:</p>
  <ul style="margin:0;padding-left:20px;color:#555">
{reasons_items}
  </ul>"""

        html_body = f"""
<div style="font-family:-apple-system,sans-serif;max-width:560px;margin:0 auto;color:#1a1a1a;line-height:1.6">
  <p>Hi,</p>

  <p>We track Google Maps rankings for <em>{category_label}</em> businesses across {city} every week.</p>

  <p>This week, <strong>{business_name}</strong> dropped from
  <strong style="color:#0066cc">#{alert['prev_rank']}</strong> to
  <strong style="color:#c0392b">#{alert['curr_rank']}</strong> —
  that's <strong>{alert['rank_change']} positions lost</strong>.</p>

  <p>When you drop out of the top 3, Google Maps stops showing your business
  without scrolling. That means fewer calls, fewer walk-ins, fewer customers.</p>
{reasons_html}

  <div style="background:#fff8f0;border-left:4px solid #e67e22;padding:12px 16px;margin:20px 0">
    <p style="margin:0;font-size:14px;color:#333">
      <strong>I've prepared a full audit report</strong> for {business_name} — it covers
      exactly why this happened, which competitors passed you, and 3 specific actions
      you can take <em>this week</em> to recover your ranking.
    </p>
  </div>

  <ul style="color:#555;font-size:14px">
    <li>Detailed root cause analysis with competitor data</li>
    <li>3 concrete recovery actions customized to your business</li>
{insight_bullets}
  </ul>

  <div style="background:#f8f9fa;border:1px solid #e0e0e0;border-radius:6px;padding:16px 20px;margin:20px 0">
    <p style="font-weight:600;margin:0 0 10px;font-size:15px">Get your report:</p>
    <table style="width:100%;border-collapse:collapse;font-size:14px">
      <tr>
        <td style="padding:8px 0;color:#333">
          <strong>Full Audit Report</strong><br>
          <span style="color:#777;font-size:13px">Complete competitive analysis + action plan (PDF)</span>
        </td>
        <td style="padding:8px 0;text-align:right;vertical-align:middle">
          <a href="{self.payment_url_audit}" style="background:#0066cc;color:#fff;padding:8px 18px;border-radius:4px;text-decoration:none;font-weight:600;font-size:14px">$10 — Get Report</a>
        </td>
      </tr>
      <tr>
        <td colspan="2" style="padding:4px 0"><hr style="border:none;border-top:1px solid #eee"></td>
      </tr>
      <tr>
        <td style="padding:8px 0;color:#333">
          <strong>Weekly Monitoring</strong><br>
          <span style="color:#777;font-size:13px">Rank tracking + alerts + monthly trend reports</span>
        </td>
        <td style="padding:8px 0;text-align:right;vertical-align:middle">
          <a href="{self.payment_url}" style="background:#fff;color:#0066cc;padding:7px 18px;border-radius:4px;text-decoration:none;font-weight:600;font-size:14px;border:1px solid #0066cc">$5/month</a>
        </td>
      </tr>
    </table>
  </div>

  <p style="font-size:13px;color:#777">Or just reply to this email — happy to answer questions.</p>

  <p>Best,<br>
  Search Sentinel</p>

  <hr style="border:none;border-top:1px solid #e5e5e5;margin:24px 0">
  <p style="font-size:11px;color:#999">
    This is a one-time commercial message from Search Sentinel.<br>
    Search Sentinel · Hillsborough, NJ 08844<br>
    To opt out of future emails, reply with "unsubscribe".
  </p>
</div>
""".strip()

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.from_name} <{self.gmail_user}>"
            msg["To"] = to_email
            msg["Reply-To"] = self.gmail_user

            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self.gmail_user, self.gmail_app_password)
                server.sendmail(self.gmail_user, [to_email], msg.as_string())

            logger.info(f"Outreach: teaser email sent to {to_email} ({business_name})")
            return True

        except Exception as e:
            logger.error(f"Outreach: failed to send to {to_email}: {e}")
            return False

    def send_fulfillment_email(
        self,
        to_email: str,
        business_name: str,
        pdf_path: Path,
    ) -> bool:
        """Send the full PDF audit after payment confirmation."""
        if not all([self.gmail_user, self.gmail_app_password, to_email]):
            return False

        subject = f"Your SEO Audit Report for {business_name}"

        html_body = f"""
<div style="font-family:-apple-system,sans-serif;max-width:560px;margin:0 auto;color:#1a1a1a;line-height:1.6">
  <p>Hi,</p>

  <p>Thank you for your purchase! Your full audit report for
  <strong>{business_name}</strong> is attached to this email.</p>

  <p>The report includes:</p>
  <ul>
    <li>Detailed root cause analysis of your ranking drop</li>
    <li>Competitor intelligence — who passed you and how</li>
    <li>3 specific recovery actions you can take this week</li>
    <li>Performance trends and market position score</li>
  </ul>

  <p>If you have any questions about the report or want help implementing
  the recommendations, just reply to this email.</p>

  <p>Best,<br>
  Search Sentinel</p>

  <hr style="border:none;border-top:1px solid #e5e5e5;margin:24px 0">
  <p style="font-size:11px;color:#999">
    Search Sentinel · Hillsborough, NJ 08844<br>
    sutraflow.org
  </p>
</div>
""".strip()

        try:
            msg = MIMEMultipart("mixed")
            msg["Subject"] = subject
            msg["From"] = f"{self.from_name} <{self.gmail_user}>"
            msg["To"] = to_email
            msg["Reply-To"] = self.gmail_user

            body_part = MIMEMultipart("alternative")
            body_part.attach(MIMEText(html_body, "html"))
            msg.attach(body_part)

            with open(pdf_path, "rb") as f:
                pdf_part = MIMEApplication(f.read(), _subtype="pdf")
                pdf_part.add_header(
                    "Content-Disposition", "attachment",
                    filename=f"SEO-Audit-{business_name.replace(' ', '-')}.pdf",
                )
                msg.attach(pdf_part)

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self.gmail_user, self.gmail_app_password)
                server.sendmail(self.gmail_user, [to_email], msg.as_string())

            logger.info(f"Fulfillment: PDF delivered to {to_email} ({business_name})")
            return True

        except Exception as e:
            logger.error(f"Fulfillment: failed to deliver to {to_email}: {e}")
            return False

    def _send_email(self, to_email: str, subject: str, body_text: str) -> bool:
        """Send a plain-text transactional email (for payment reminders, notifications)."""
        if not all([self.gmail_user, self.gmail_app_password, to_email]):
            return False
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.from_name} <{self.gmail_user}>"
            msg["To"] = to_email
            msg["Reply-To"] = self.gmail_user
            msg.attach(MIMEText(body_text, "plain"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self.gmail_user, self.gmail_app_password)
                server.sendmail(self.gmail_user, [to_email], msg.as_string())
            logger.info(f"Transactional email sent to {to_email}: {subject}")
            return True
        except Exception as e:
            logger.error(f"Transactional email failed to {to_email}: {e}")
            return False

    def _load_customers(self) -> dict:
        """Load the customer registry to check subscription status."""
        customers_file = Path(__file__).parent.parent / "data" / "customers.json"
        try:
            with open(customers_file) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"customers": []}

    def is_active_subscriber(self, email: str) -> bool:
        """Check if an email belongs to an active paying subscriber."""
        data = self._load_customers()
        email_lower = email.lower().strip()
        for c in data["customers"]:
            if c["email"].lower().strip() == email_lower and c["status"] == "active":
                return True
        return False

    def get_subscriber_info(self, email: str) -> dict | None:
        """Get subscriber record if they are active."""
        data = self._load_customers()
        email_lower = email.lower().strip()
        for c in data["customers"]:
            if c["email"].lower().strip() == email_lower and c["status"] == "active":
                return c
        return None

    def send_subscriber_report_email(
        self,
        to_email: str,
        business_name: str,
        alert: dict,
        pdf_path: Path,
    ) -> bool:
        """Send full PDF report to active subscriber for FREE (no payment needed).
        This is the key differentiator: subscribers get reports automatically."""
        if not all([self.gmail_user, self.gmail_app_password, to_email]):
            return False

        category_parts = alert["category_key"].split("_")
        city = category_parts[0].title() if category_parts else "your city"

        subject = f"Rank Drop Alert: {business_name} — full report attached"

        html_body = f"""
<div style="font-family:-apple-system,sans-serif;max-width:560px;margin:0 auto;color:#1a1a1a;line-height:1.6">
  <p>Hi,</p>

  <p>Your weekly monitoring detected a change for <strong>{business_name}</strong>:</p>

  <div style="background:#fff8f0;border-left:4px solid #e67e22;padding:12px 16px;margin:16px 0">
    <p style="margin:0;font-size:15px">
      Ranking moved from <strong style="color:#0066cc">#{alert['prev_rank']}</strong> to
      <strong style="color:#c0392b">#{alert['curr_rank']}</strong>
      — <strong>{alert['rank_change']} position{'s' if alert['rank_change'] != 1 else ''} lost</strong>
    </p>
  </div>

  <p><strong>Your full audit report is attached to this email</strong> — it includes
  root cause analysis, competitor intelligence, and specific recovery actions.</p>

  <p style="font-size:13px;color:#555">As an active subscriber, you receive this
  report automatically at no additional cost. Your monitoring continues weekly.</p>

  <p>Best,<br>
  Search Sentinel</p>

  <hr style="border:none;border-top:1px solid #e5e5e5;margin:24px 0">
  <p style="font-size:11px;color:#999">
    Search Sentinel · Weekly Monitoring Report<br>
    sutraflow.org/sentinel<br>
    To manage your subscription, reply with "manage".
  </p>
</div>
""".strip()

        try:
            msg = MIMEMultipart("mixed")
            msg["Subject"] = subject
            msg["From"] = f"{self.from_name} <{self.gmail_user}>"
            msg["To"] = to_email
            msg["Reply-To"] = self.gmail_user

            body_part = MIMEMultipart("alternative")
            body_part.attach(MIMEText(html_body, "html"))
            msg.attach(body_part)

            with open(pdf_path, "rb") as f:
                pdf_part = MIMEApplication(f.read(), _subtype="pdf")
                pdf_part.add_header(
                    "Content-Disposition", "attachment",
                    filename=f"SearchSentinel-Audit-{business_name.replace(' ', '-')}.pdf",
                )
                msg.attach(pdf_part)

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self.gmail_user, self.gmail_app_password)
                server.sendmail(self.gmail_user, [to_email], msg.as_string())

            logger.info(f"Subscriber report sent to {to_email} ({business_name})")
            return True

        except Exception as e:
            logger.error(f"Subscriber report failed for {to_email}: {e}")
            return False

    def send_allclear_email(
        self,
        to_email: str,
        business_name: str,
        current_rank: int,
        category: str,
        city: str,
    ) -> bool:
        """Send weekly all-clear email to subscriber when rank is stable.
        This keeps subscribers engaged even when nothing changes."""
        if not all([self.gmail_user, self.gmail_app_password, to_email]):
            return False

        subject = f"Weekly Update: {business_name} — ranking stable at #{current_rank}"

        rank_status = "in the top 3" if current_rank <= 3 else f"at #{current_rank}"

        html_body = f"""
<div style="font-family:-apple-system,sans-serif;max-width:560px;margin:0 auto;color:#1a1a1a;line-height:1.6">
  <p>Hi,</p>

  <p>Your weekly scan for <strong>{business_name}</strong> is complete.</p>

  <div style="background:#f0fdf4;border-left:4px solid #16a34a;padding:12px 16px;margin:16px 0">
    <p style="margin:0;font-size:15px;color:#166534">
      <strong>All clear</strong> — your Google Maps ranking held steady
      {rank_status} for <em>{category}</em> in {city}.
    </p>
  </div>

  <p>No changes detected this week. Your competitors' positions remained stable
  and no significant review activity was flagged.</p>

  <p style="font-size:14px;color:#555"><strong>We're still watching.</strong> If anything
  changes next week, you'll get a full alert with detailed analysis.</p>

  <p>Best,<br>
  Search Sentinel</p>

  <hr style="border:none;border-top:1px solid #e5e5e5;margin:24px 0">
  <p style="font-size:11px;color:#999">
    Search Sentinel · Weekly Monitoring Report<br>
    sutraflow.org/sentinel<br>
    To manage your subscription, reply with "manage".
  </p>
</div>
""".strip()

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.from_name} <{self.gmail_user}>"
            msg["To"] = to_email
            msg["Reply-To"] = self.gmail_user
            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self.gmail_user, self.gmail_app_password)
                server.sendmail(self.gmail_user, [to_email], msg.as_string())

            logger.info(f"All-clear email sent to {to_email} ({business_name})")
            return True

        except Exception as e:
            logger.error(f"All-clear email failed for {to_email}: {e}")
            return False

    def process_batch_teasers(self, alerts: list[dict]) -> dict:
        """
        Send teaser emails for rank drops — NO PDF generation.

        For each alert:
        - Active subscriber → send a subscriber drop-notification email (no PDF attached;
          the subscriber can reply or will get a PDF from the webhook if they want one)
        - Non-subscriber → send teaser email with Stripe payment links

        PDFs are generated on-demand AFTER payment via webhook_server.py.
        This keeps the weekly pipeline fast and avoids burning Claude API quota.

        Returns summary stats + list of contacted businesses.
        """
        sent = 0
        no_email = 0
        failed = 0
        subscriber_notified = 0
        contacted = []

        for alert in alerts[:self.max_emails_per_run]:
            email = self.find_email_from_website(alert.get("website", ""))

            if not email:
                no_email += 1
                logger.info(f"Outreach: no email found for {alert['business_name']}")
                continue

            # CHECK: Is this an active subscriber?
            if self.is_active_subscriber(email):
                # Subscribers get a drop-notification email (no PDF — generated on demand)
                success = self.send_subscriber_drop_notification(
                    email, alert["business_name"], alert
                )
                if success:
                    subscriber_notified += 1
                    sent += 1
                    contacted.append({
                        "email": email,
                        "business_name": alert["business_name"],
                        "category_key": alert["category_key"],
                        "alert_data": alert,
                        "type": "subscriber_report",
                    })
                else:
                    failed += 1
            else:
                success = self.send_teaser_email(email, alert["business_name"], alert)
                if success:
                    sent += 1
                    contacted.append({
                        "email": email,
                        "business_name": alert["business_name"],
                        "category_key": alert["category_key"],
                        "alert_data": alert,
                        "type": "teaser",
                    })
                else:
                    failed += 1

            time.sleep(3)  # rate limit between emails

        summary = {
            "sent": sent,
            "no_email": no_email,
            "failed": failed,
            "subscriber_notified": subscriber_notified,
            "contacted": contacted,
        }
        logger.info(
            f"Outreach batch complete: sent={sent} (subscribers={subscriber_notified}), "
            f"no_email={no_email}, failed={failed}"
        )
        return summary

    def send_subscriber_drop_notification(
        self,
        to_email: str,
        business_name: str,
        alert: dict,
    ) -> bool:
        """Send drop notification to active subscriber — no PDF attached.
        The full PDF will be generated and sent automatically (subscriber benefit)
        by a background job or on-demand request."""
        if not all([self.gmail_user, self.gmail_app_password, to_email]):
            return False

        category_parts = alert["category_key"].split("_")
        city = category_parts[0].title() if category_parts else "your city"

        subject = f"Rank Drop Alert: {business_name} — #{alert['prev_rank']} to #{alert['curr_rank']}"

        reasons = alert.get("reasons", [])
        reasons_html = ""
        if reasons:
            reasons_items = "\n".join(f"    <li>{r}</li>" for r in reasons[:3])
            reasons_html = f"""
  <p style="font-weight:600;margin:16px 0 6px">Detected signals:</p>
  <ul style="margin:0;padding-left:20px;color:#555">
{reasons_items}
  </ul>"""

        html_body = f"""
<div style="font-family:-apple-system,sans-serif;max-width:560px;margin:0 auto;color:#1a1a1a;line-height:1.6">
  <p>Hi,</p>

  <p>Your weekly monitoring detected a ranking change for <strong>{business_name}</strong>:</p>

  <div style="background:#fff8f0;border-left:4px solid #e67e22;padding:12px 16px;margin:16px 0">
    <p style="margin:0;font-size:15px">
      Ranking moved from <strong style="color:#0066cc">#{alert['prev_rank']}</strong> to
      <strong style="color:#c0392b">#{alert['curr_rank']}</strong>
      — <strong>{alert['rank_change']} position{'s' if alert['rank_change'] != 1 else ''} lost</strong>
    </p>
  </div>
{reasons_html}

  <p><strong>Your full audit report is being generated</strong> and will be emailed
  to you shortly. As an active subscriber, this is included at no extra cost.</p>

  <p style="font-size:13px;color:#555">If you need the report urgently, reply to
  this email and we'll prioritize delivery.</p>

  <p>Best,<br>
  Search Sentinel</p>

  <hr style="border:none;border-top:1px solid #e5e5e5;margin:24px 0">
  <p style="font-size:11px;color:#999">
    Search Sentinel · Weekly Monitoring Report<br>
    sutraflow.org/sentinel<br>
    To manage your subscription, reply with "manage".
  </p>
</div>
""".strip()

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.from_name} <{self.gmail_user}>"
            msg["To"] = to_email
            msg["Reply-To"] = self.gmail_user
            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self.gmail_user, self.gmail_app_password)
                server.sendmail(self.gmail_user, [to_email], msg.as_string())

            logger.info(f"Subscriber drop notification sent to {to_email} ({business_name})")
            return True

        except Exception as e:
            logger.error(f"Subscriber notification failed for {to_email}: {e}")
            return False
