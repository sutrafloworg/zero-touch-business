"""
Monitor Agent — tracks pipeline health and sends status alerts.
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
        alert_email: str,
        gmail_user: str,
        gmail_app_password: str,
    ):
        self.state_file = state_file
        self.alert_email = alert_email
        self.gmail_user = gmail_user
        self.gmail_app_password = gmail_app_password

    def _load_state(self) -> dict:
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_state(self, updates: dict) -> None:
        state = self._load_state()
        state.update(updates)
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2, default=str)

    def _send_email(self, subject: str, body: str) -> bool:
        if not all([self.gmail_user, self.gmail_app_password, self.alert_email]):
            return False
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"LocalRank Sentinel <{self.gmail_user}>"
            msg["To"] = self.alert_email
            msg.attach(MIMEText(body, "html"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self.gmail_user, self.gmail_app_password)
                server.sendmail(self.gmail_user, [self.alert_email], msg.as_string())
            return True
        except Exception as e:
            logger.error(f"Monitor email failed: {e}")
            return False

    def record_run(
        self,
        scans: int,
        alerts: int,
        reports_generated: int,
        emails_sent: int,
        outreach_summary: dict,
    ) -> None:
        """Record pipeline run results and send summary to owner."""
        now = datetime.now(timezone.utc)
        state = self._load_state()

        state.update({
            "last_run": now.isoformat(),
            "total_runs": state.get("total_runs", 0) + 1,
            "last_scans": scans,
            "last_alerts": alerts,
            "last_reports": reports_generated,
            "last_emails_sent": emails_sent,
            "total_emails_sent": state.get("total_emails_sent", 0) + emails_sent,
            "total_reports_generated": state.get("total_reports_generated", 0) + reports_generated,
            "consecutive_failures": 0,
            "last_status": "OK",
        })
        self._save_state(state)

        # Send summary email to owner
        body = f"""
<div style="font-family:-apple-system,sans-serif;max-width:500px;margin:0 auto">
  <h2 style="color:#0f0f0f">LocalRank Sentinel — Run Complete</h2>
  <p style="color:#666">{now.strftime('%B %d, %Y at %H:%M UTC')}</p>
  <table style="width:100%;border-collapse:collapse">
    <tr><td style="padding:8px;border-bottom:1px solid #eee">Categories scanned</td>
        <td style="padding:8px;border-bottom:1px solid #eee;font-weight:600;text-align:right">{scans}</td></tr>
    <tr><td style="padding:8px;border-bottom:1px solid #eee">Rank drops detected</td>
        <td style="padding:8px;border-bottom:1px solid #eee;font-weight:600;text-align:right">{alerts}</td></tr>
    <tr><td style="padding:8px;border-bottom:1px solid #eee">Audit PDFs generated</td>
        <td style="padding:8px;border-bottom:1px solid #eee;font-weight:600;text-align:right">{reports_generated}</td></tr>
    <tr><td style="padding:8px;border-bottom:1px solid #eee">Emails sent</td>
        <td style="padding:8px;border-bottom:1px solid #eee;font-weight:600;text-align:right">{emails_sent}</td></tr>
    <tr><td style="padding:8px;border-bottom:1px solid #eee">No email found</td>
        <td style="padding:8px;border-bottom:1px solid #eee;text-align:right">{outreach_summary.get('no_email', 0)}</td></tr>
  </table>
  <p style="color:#999;font-size:12px;margin-top:16px">
    Total emails sent all time: {state.get('total_emails_sent', 0)}<br>
    Total reports generated: {state.get('total_reports_generated', 0)}<br>
    Total pipeline runs: {state.get('total_runs', 0)}
  </p>
</div>"""
        self._send_email(
            f"[LocalRank] {alerts} drops detected, {emails_sent} audits sent",
            body,
        )

    def record_failure(self, error: str) -> None:
        state = self._load_state()
        failures = state.get("consecutive_failures", 0) + 1
        state.update({
            "last_run": datetime.now(timezone.utc).isoformat(),
            "consecutive_failures": failures,
            "last_status": f"FAILED: {error}",
        })
        self._save_state(state)

        if failures >= 3:
            self._send_email(
                f"[CRITICAL] LocalRank Sentinel — {failures} consecutive failures",
                f"<p>Error: {error}</p><p>Check GitHub Actions logs and Secrets.</p>",
            )
