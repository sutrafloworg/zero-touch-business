"""
Monitor Agent — health checks and self-correction for the SEO site.

Checks:
  1. Articles were actually written (files exist)
  2. State is consistent (no orphaned in_progress keywords)
  3. Site is reachable (HTTP check on live domain)
  4. Sends digest report (every 4 weeks)

Self-correction:
  - Orphaned 'in_progress' keywords → reset to 'pending' (handled by KeywordAgent)
  - Site unreachable → send alert
  - 0 articles published after multiple runs → escalate alert
"""
import json
import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


class MonitorAgent:
    def __init__(
        self,
        state_file: Path,
        content_dir: Path,
        site_domain: str,
        alert_email: str,
        gmail_user: str,
        gmail_app_password: str,
    ):
        self.state_file = state_file
        self.content_dir = content_dir
        self.site_domain = site_domain
        self.alert_email = alert_email
        self.gmail_user = gmail_user
        self.gmail_app_password = gmail_app_password

    def _load_state(self) -> dict:
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_state(self, updates: dict) -> None:
        state = self._load_state()
        state.update(updates)
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2, default=str)

    def _send_email(self, subject: str, body: str) -> bool:
        if not all([self.gmail_user, self.gmail_app_password, self.alert_email]):
            logger.warning("Email not configured — skipping alert")
            return False
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.gmail_user
            msg["To"] = self.alert_email
            msg.attach(MIMEText(body, "plain"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self.gmail_user, self.gmail_app_password)
                server.sendmail(self.gmail_user, [self.alert_email], msg.as_string())
            return True
        except Exception as e:
            logger.error(f"Email send failed: {e}")
            return False

    def check_site_health(self) -> bool:
        """HTTP check on the live site."""
        if not self.site_domain or self.site_domain == "your-domain.com":
            logger.info("Site domain not configured — skipping health check")
            return True

        try:
            url = f"https://{self.site_domain}"
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                logger.info(f"Site health: OK ({resp.status_code})")
                return True
            else:
                logger.warning(f"Site health: unexpected status {resp.status_code}")
                self._send_email(
                    "[WARNING] SEO Site — Unexpected HTTP Status",
                    f"Site {url} returned HTTP {resp.status_code}.\n"
                    "Check Cloudflare Pages dashboard.",
                )
                return False
        except requests.ConnectionError:
            logger.error(f"Site health: UNREACHABLE — {self.site_domain}")
            self._send_email(
                "[CRITICAL] SEO Site — Site Unreachable",
                f"https://{self.site_domain} is not responding.\n"
                "Check Cloudflare Pages for build errors.",
            )
            return False
        except Exception as e:
            logger.warning(f"Site health check failed: {e}")
            return True  # Non-critical — don't block pipeline

    def check_and_heal(
        self,
        published_this_run: list[str],
        expected_count: int,
    ) -> bool:
        """
        Post-pipeline health check.
        Returns True if healthy.
        """
        now = datetime.now(timezone.utc).isoformat()
        state = self._load_state()
        success = True

        # ── Check 1: Articles actually written ──────────────────────────────
        actual_files = [
            slug for slug in published_this_run
            if (self.content_dir / f"{slug}.md").exists()
        ]

        if len(actual_files) < expected_count:
            missing = expected_count - len(actual_files)
            logger.warning(f"Monitor: {missing} articles failed to write this run")
            failures = state.get("consecutive_failures", 0) + 1
            self._save_state({"consecutive_failures": failures, "last_status": "PARTIAL"})

            if failures >= 3:
                self._send_email(
                    "[CRITICAL] SEO Site — 3 Consecutive Partial Failures",
                    f"The SEO pipeline has had partial failures {failures} runs in a row.\n\n"
                    "Check GitHub Actions logs for Claude API errors.\n"
                    "Verify ANTHROPIC_API_KEY is valid and has credits.",
                )
            success = False
        else:
            self._save_state({
                "consecutive_failures": 0,
                "last_status": "OK",
                "last_run": now,
            })
            logger.info(f"Monitor: all {len(actual_files)} articles confirmed written")

        # ── Check 2: Site health ────────────────────────────────────────────
        self.check_site_health()

        # ── Check 3: Count total articles on site ───────────────────────────
        total_articles = len(list(self.content_dir.glob("*.md")))
        logger.info(f"Monitor: total articles in content dir: {total_articles}")
        self._save_state({"total_articles_on_disk": total_articles})

        return success

    def send_monthly_digest(self) -> None:
        """Send monthly performance summary."""
        state = self._load_state()
        total = state.get("articles_published", 0)
        body = (
            f"Monthly SEO Site Digest\n"
            f"{'=' * 50}\n\n"
            f"Total articles published: {total}\n"
            f"Articles on disk: {state.get('total_articles_on_disk', 0)}\n"
            f"Site domain: {self.site_domain}\n"
            f"Last status: {state.get('last_status', 'unknown')}\n\n"
            f"Check your affiliate dashboards for commission data.\n"
            f"Check Google Search Console for impressions + clicks:\n"
            f"https://search.google.com/search-console\n\n"
            f"Keep growing — target: 200+ articles for meaningful SEO traffic."
        )
        self._send_email("[Monthly Report] SEO Affiliate Site", body)
