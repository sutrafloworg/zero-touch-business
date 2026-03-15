"""
Monitor Agent — runs after every pipeline execution.

Responsibilities:
  1. Verify the newsletter was actually sent (not stuck in draft/fallback)
  2. Update state.json with run stats
  3. Send alert email if anything went wrong
  4. Self-correct: queue failed sends for retry next run
  5. Generate a weekly digest report (sent to the owner's email)

Self-correction triggers:
  - broadcast_id starts with "fallback:" → critical failure, alert + queue retry
  - consecutive_failures >= 3 → alert with detailed diagnostics
  - subscriber_count < 0 → API issue, alert
"""
import json
import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)


class MonitorAgent:
    def __init__(
        self,
        state_file: Path,
        logs_dir: Path,
        alert_email: str,
        gmail_user: str,
        gmail_app_password: str,
        newsletter_name: str,
    ):
        self.state_file = state_file
        self.logs_dir = logs_dir
        self.alert_email = alert_email
        self.gmail_user = gmail_user
        self.gmail_app_password = gmail_app_password
        self.newsletter_name = newsletter_name

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
        """Send alert email via Gmail SMTP."""
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

            logger.info(f"Alert email sent: {subject}")
            return True
        except Exception as e:
            logger.error(f"Failed to send alert email: {e}")
            return False

    # ── Self-Correction Logic ───────────────────────────────────────────────────
    def check_and_heal(
        self,
        broadcast_id: str,
        subscriber_count: int,
        issues_published: int,
    ) -> bool:
        """
        Run post-pipeline health checks.
        Returns True if everything is healthy.
        """
        state = self._load_state()
        now = datetime.now(timezone.utc).isoformat()
        success = True

        # ── Check 1: Was newsletter actually sent? ──────────────────────────
        if broadcast_id.startswith("fallback:"):
            fallback_path = broadcast_id.replace("fallback:", "")
            failures = state.get("consecutive_failures", 0) + 1
            self._save_state({
                "consecutive_failures": failures,
                "last_run": now,
                "last_status": "FAILED",
                "pending_retry": state.get("pending_retry", []) + [fallback_path],
            })

            alert_body = (
                f"ALERT: {self.newsletter_name} failed to publish.\n\n"
                f"Issue saved at: {fallback_path}\n"
                f"Consecutive failures: {failures}\n"
                f"Action needed: Check your Kit API credentials in GitHub Secrets.\n\n"
                f"The system will automatically retry next scheduled run."
            )
            self._send_email(
                f"[ACTION REQUIRED] {self.newsletter_name} — Publish Failed",
                alert_body,
            )
            success = False

        else:
            # Successful publish
            self._save_state({
                "consecutive_failures": 0,
                "last_run": now,
                "last_status": "OK",
                "last_broadcast_id": broadcast_id,
                "issues_published": issues_published,
                "total_subscribers_last_check": subscriber_count,
                "pending_retry": [],
            })
            logger.info(f"Monitor: Newsletter published OK. Broadcast ID: {broadcast_id}")

        # ── Check 2: Subscriber count sanity ───────────────────────────────
        if subscriber_count < 0:
            self._send_email(
                f"[WARNING] {self.newsletter_name} — API Auth Issue",
                "Could not fetch subscriber count. Kit API may have an authentication problem.\n"
                "Check KIT_API_SECRET in GitHub Secrets.",
            )

        # ── Check 3: Consecutive failure escalation ─────────────────────────
        current_failures = self._load_state().get("consecutive_failures", 0)
        if current_failures >= 3:
            self._send_email(
                f"[CRITICAL] {self.newsletter_name} — 3 Consecutive Failures",
                f"The newsletter automation has failed {current_failures} times in a row.\n\n"
                "Likely causes:\n"
                "1. Kit API secret expired or revoked\n"
                "2. Claude API key invalid or out of credits\n"
                "3. GitHub Actions runner issue\n\n"
                "Please check GitHub Actions logs and Secrets.",
            )

        return success

    def send_weekly_digest(self, subscriber_count: int, broadcast_id: str) -> None:
        """Send weekly performance summary to owner."""
        state = self._load_state()
        body = (
            f"Weekly Digest — {self.newsletter_name}\n"
            f"{'=' * 50}\n\n"
            f"Issues published total: {state.get('issues_published', 0)}\n"
            f"Active subscribers: {subscriber_count}\n"
            f"Last broadcast ID: {broadcast_id}\n"
            f"Status: {state.get('last_status', 'unknown')}\n\n"
            f"Check your Kit dashboard for open rates and click stats:\n"
            f"https://app.kit.com/broadcasts\n\n"
            f"Affiliate link performance is in your individual affiliate dashboards."
        )
        self._send_email(f"[Weekly Report] {self.newsletter_name}", body)
