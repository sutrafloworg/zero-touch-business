"""
Outreach Agent — sends personalized audit emails to businesses that dropped rank.

Extracts contact info from:
  1. Google Business Profile data (phone, website from SerpAPI)
  2. Business website (scrapes for email addresses)

CAN-SPAM compliant:
  - Identifies as commercial message
  - Includes physical address
  - Includes opt-out mechanism
  - Honest subject line
"""
import logging
import re
import smtplib
import time
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Email addresses to never contact
BLOCKLIST_PATTERNS = [
    "noreply@", "no-reply@", "info@example", "test@",
    "admin@", "webmaster@", "support@",
]


class OutreachAgent:
    def __init__(
        self,
        gmail_user: str,
        gmail_app_password: str,
        from_name: str = "LocalRank Sentinel",
        max_emails_per_run: int = 10,
    ):
        self.gmail_user = gmail_user
        self.gmail_app_password = gmail_app_password
        self.from_name = from_name
        self.max_emails_per_run = max_emails_per_run

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
                headers={"User-Agent": "Mozilla/5.0 (compatible; LocalRankBot/1.0)"},
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
                            headers={"User-Agent": "Mozilla/5.0 (compatible; LocalRankBot/1.0)"},
                        )
                        if contact_resp.status_code == 200:
                            emails += re.findall(
                                r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
                                contact_resp.text,
                            )
                    except Exception:
                        continue

            # Filter out blocklisted patterns
            valid_emails = [
                e for e in emails
                if not any(p in e.lower() for p in BLOCKLIST_PATTERNS)
            ]

            if valid_emails:
                email = valid_emails[0]
                logger.info(f"Found email {email} on {website_url}")
                return email

        except Exception as e:
            logger.debug(f"Could not scrape {website_url}: {e}")

        return None

    def send_audit_email(
        self,
        to_email: str,
        business_name: str,
        alert: dict,
        pdf_path: Path,
    ) -> bool:
        """Send personalized audit email with PDF attachment. CAN-SPAM compliant."""
        if not all([self.gmail_user, self.gmail_app_password, to_email]):
            return False

        category_parts = alert["category_key"].split("_")
        city = category_parts[0].title() if category_parts else "your city"

        subject = f"Your {business_name} Google ranking dropped — free audit inside"

        html_body = f"""
<div style="font-family:-apple-system,sans-serif;max-width:560px;margin:0 auto;color:#1a1a1a;line-height:1.6">
  <p>Hi,</p>

  <p>I noticed <strong>{business_name}</strong> dropped from
  <strong>#{alert['prev_rank']}</strong> to <strong>#{alert['curr_rank']}</strong>
  in Google Maps for {category_parts[2] if len(category_parts) > 2 else 'your category'}
  searches in {city}.</p>

  <p>I put together a quick audit (attached as PDF) that explains:</p>
  <ul>
    <li>Why the drop likely happened</li>
    <li>What your competitors did differently</li>
    <li>3 specific actions you can take this week to recover</li>
  </ul>

  <p>Take a look — it's genuinely useful even if we never speak again.</p>

  <p>If you'd like weekly monitoring so you catch these drops early,
  just reply to this email and I'll set it up.</p>

  <p>Best,<br>
  LocalRank Sentinel</p>

  <hr style="border:none;border-top:1px solid #e5e5e5;margin:24px 0">
  <p style="font-size:11px;color:#999">
    This is a one-time commercial message from LocalRank Sentinel.<br>
    LocalRank Sentinel · 1111 S Figueroa St · Los Angeles, CA 90015<br>
    To opt out of future emails, reply with "unsubscribe".
  </p>
</div>
""".strip()

        try:
            msg = MIMEMultipart("mixed")
            msg["Subject"] = subject
            msg["From"] = f"{self.from_name} <{self.gmail_user}>"
            msg["To"] = to_email
            msg["Reply-To"] = self.gmail_user

            # HTML body
            body_part = MIMEMultipart("alternative")
            body_part.attach(MIMEText(html_body, "html"))
            msg.attach(body_part)

            # PDF attachment
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

            logger.info(f"Outreach: audit email sent to {to_email} ({business_name})")
            return True

        except Exception as e:
            logger.error(f"Outreach: failed to send to {to_email}: {e}")
            return False

    def process_batch(self, report_results: list[dict]) -> dict:
        """
        For each generated audit PDF, find email and send.
        Returns summary stats.
        """
        sent = 0
        no_email = 0
        failed = 0

        for item in report_results[:self.max_emails_per_run]:
            alert = item["alert"]
            pdf_path = item["pdf_path"]

            email = self.find_email_from_website(alert.get("website", ""))

            if not email:
                no_email += 1
                logger.info(f"Outreach: no email found for {alert['business_name']}")
                continue

            success = self.send_audit_email(email, alert["business_name"], alert, Path(pdf_path))
            if success:
                sent += 1
            else:
                failed += 1

            time.sleep(3)  # rate limit between emails

        summary = {"sent": sent, "no_email": no_email, "failed": failed}
        logger.info(f"Outreach batch complete: {summary}")
        return summary
