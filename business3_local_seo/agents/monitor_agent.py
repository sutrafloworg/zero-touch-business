"""
Monitor Agent — tracks pipeline health and sends comprehensive status emails.

After every pipeline run, sends a full dashboard email to the owner including:
  - This run's results
  - All-time metrics
  - Fulfillment/payment status
  - Search API quota
  - Individual business details (who got teasers, who's pending payment)
"""
import json
import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"


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

    def _load_json(self, path: Path, default=None):
        try:
            return json.loads(path.read_text())
        except Exception:
            return default if default is not None else {}

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
        """Record pipeline run results and send comprehensive dashboard email."""
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

        # Gather dashboard data
        pending_data = self._load_json(DATA_DIR / "pending_reports.json", {"reports": []})
        usage = self._load_json(DATA_DIR / "search_usage.json", {})
        contacts = self._load_json(DATA_DIR / "contacts.json", {})
        rankings = self._load_json(DATA_DIR / "rankings_history.json", {})

        reports = pending_data.get("reports", [])
        pending_count = sum(1 for r in reports if r.get("status") == "pending")
        delivered_count = sum(1 for r in reports if r.get("status") == "delivered")
        failed_count = sum(1 for r in reports if r.get("status") == "failed")
        categories_tracked = len(rankings)
        with_history = sum(1 for v in rankings.values()
                          if isinstance(v, dict) and len(v.get("snapshots", [])) >= 2)

        serpapi_used = usage.get("serpapi", 0)
        valueserp_used = usage.get("valueserp", 0)

        # Build contacted businesses list
        contacted = outreach_summary.get("contacted", [])
        contacted_html = ""
        if contacted:
            rows = ""
            for c in contacted[:15]:
                biz = c.get("business_name", "Unknown")[:35]
                email = c.get("email", "no-email")
                rows += f"""<tr>
                    <td style="padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:13px">{biz}</td>
                    <td style="padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:13px;color:#0066cc">{email}</td>
                </tr>"""
            contacted_html = f"""
            <div style="margin-top:20px">
                <h3 style="color:#333;font-size:14px;margin-bottom:8px">Businesses Contacted This Run</h3>
                <table style="width:100%;border-collapse:collapse;border:1px solid #e0e0e0">
                    <tr style="background:#f7f8fa">
                        <th style="padding:8px;text-align:left;font-size:12px;color:#666">Business</th>
                        <th style="padding:8px;text-align:left;font-size:12px;color:#666">Email</th>
                    </tr>
                    {rows}
                </table>
                {"<p style='font-size:11px;color:#999'>Showing first 15</p>" if len(contacted) > 15 else ""}
            </div>"""

        # Pending reports section
        pending_html = ""
        pending_reports = [r for r in reports if r.get("status") == "pending"]
        if pending_reports:
            rows = ""
            for r in sorted(pending_reports, key=lambda x: x.get("created_at", ""), reverse=True)[:10]:
                biz = r.get("business_name", "Unknown")[:35]
                email = r.get("email", "no-email")
                created = r.get("created_at", "")[:10]
                rows += f"""<tr>
                    <td style="padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:13px">{biz}</td>
                    <td style="padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:13px">{email}</td>
                    <td style="padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:13px;color:#d97706">Awaiting payment</td>
                    <td style="padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:12px;color:#999">{created}</td>
                </tr>"""
            pending_html = f"""
            <div style="margin-top:20px">
                <h3 style="color:#333;font-size:14px;margin-bottom:8px">Pending Reports (Awaiting Payment)</h3>
                <table style="width:100%;border-collapse:collapse;border:1px solid #e0e0e0">
                    <tr style="background:#f7f8fa">
                        <th style="padding:8px;text-align:left;font-size:12px;color:#666">Business</th>
                        <th style="padding:8px;text-align:left;font-size:12px;color:#666">Email</th>
                        <th style="padding:8px;text-align:left;font-size:12px;color:#666">Status</th>
                        <th style="padding:8px;text-align:left;font-size:12px;color:#666">Sent</th>
                    </tr>
                    {rows}
                </table>
            </div>"""

        # Build full dashboard email
        body = f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:600px;margin:0 auto;color:#333">
    <!-- Header -->
    <div style="background:#0f0f0f;padding:20px 24px;border-radius:8px 8px 0 0">
        <h1 style="color:#fff;font-size:20px;margin:0">LocalRank Sentinel</h1>
        <p style="color:#999;font-size:12px;margin:4px 0 0">Pipeline Run Complete</p>
    </div>
    <div style="background:#0066cc;height:3px"></div>

    <!-- Status badge -->
    <div style="padding:16px 24px;background:#f0fff4;border-left:4px solid #1e8232">
        <span style="color:#1e8232;font-weight:600;font-size:14px">STATUS: OK</span>
        <span style="color:#666;font-size:13px;margin-left:12px">{now.strftime('%B %d, %Y at %H:%M UTC')}</span>
    </div>

    <!-- This Run -->
    <div style="padding:20px 24px">
        <h3 style="color:#333;font-size:14px;margin:0 0 12px;border-bottom:2px solid #0066cc;padding-bottom:6px">This Run</h3>
        <table style="width:100%;border-collapse:collapse">
            <tr><td style="padding:8px;border-bottom:1px solid #eee">Categories scanned</td>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:600;text-align:right">{scans}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee">Rank drops detected</td>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:600;text-align:right;color:{'#b41e1e' if alerts > 0 else '#1e8232'}">{alerts}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee">Audit PDFs generated</td>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:600;text-align:right">{reports_generated}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee">Teaser emails sent</td>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:600;text-align:right">{emails_sent}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee">No email found (skipped)</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;color:#999">{outreach_summary.get('no_email', 0)}</td></tr>
        </table>
    </div>

    {contacted_html}

    <!-- Fulfillment & Revenue -->
    <div style="padding:20px 24px;background:#f7f8fa;margin:0 -0px">
        <h3 style="color:#333;font-size:14px;margin:0 0 12px;border-bottom:2px solid #0066cc;padding-bottom:6px">Payment & Fulfillment</h3>
        <table style="width:100%;border-collapse:collapse">
            <tr><td style="padding:8px;border-bottom:1px solid #e0e0e0">Awaiting payment</td>
                <td style="padding:8px;border-bottom:1px solid #e0e0e0;font-weight:600;text-align:right;color:#d97706">{pending_count}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #e0e0e0">Delivered (paid)</td>
                <td style="padding:8px;border-bottom:1px solid #e0e0e0;font-weight:600;text-align:right;color:#1e8232">{delivered_count}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #e0e0e0">Failed delivery</td>
                <td style="padding:8px;border-bottom:1px solid #e0e0e0;text-align:right;color:#b41e1e">{failed_count}</td></tr>
            <tr><td style="padding:8px;font-weight:600">Total reports in pipeline</td>
                <td style="padding:8px;font-weight:600;text-align:right">{len(reports)}</td></tr>
        </table>
    </div>

    {pending_html}

    <!-- All-Time Metrics -->
    <div style="padding:20px 24px">
        <h3 style="color:#333;font-size:14px;margin:0 0 12px;border-bottom:2px solid #0066cc;padding-bottom:6px">All-Time Metrics</h3>
        <table style="width:100%;border-collapse:collapse">
            <tr><td style="padding:8px;border-bottom:1px solid #eee">Total pipeline runs</td>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:600;text-align:right">{state.get('total_runs', 0)}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee">Total emails sent</td>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:600;text-align:right">{state.get('total_emails_sent', 0)}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee">Total reports generated</td>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:600;text-align:right">{state.get('total_reports_generated', 0)}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee">Contacts discovered</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right">{len(contacts)}</td></tr>
        </table>
    </div>

    <!-- Search API Quota -->
    <div style="padding:20px 24px;background:#f7f8fa">
        <h3 style="color:#333;font-size:14px;margin:0 0 12px;border-bottom:2px solid #0066cc;padding-bottom:6px">Search API Quota ({usage.get('month', 'unknown')})</h3>
        <table style="width:100%;border-collapse:collapse">
            <tr><td style="padding:8px;border-bottom:1px solid #e0e0e0">SerpAPI</td>
                <td style="padding:8px;border-bottom:1px solid #e0e0e0;text-align:right">{serpapi_used}/245 used
                    <span style="color:{'#b41e1e' if serpapi_used > 200 else '#1e8232'};font-weight:600"> ({245 - serpapi_used} remaining)</span></td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #e0e0e0">ValueSERP</td>
                <td style="padding:8px;border-bottom:1px solid #e0e0e0;text-align:right">{valueserp_used}/95 used
                    <span style="color:{'#b41e1e' if valueserp_used > 75 else '#1e8232'};font-weight:600"> ({95 - valueserp_used} remaining)</span></td></tr>
        </table>
    </div>

    <!-- Rankings Coverage -->
    <div style="padding:20px 24px">
        <h3 style="color:#333;font-size:14px;margin:0 0 12px;border-bottom:2px solid #0066cc;padding-bottom:6px">Rankings Coverage</h3>
        <table style="width:100%;border-collapse:collapse">
            <tr><td style="padding:8px;border-bottom:1px solid #eee">Categories tracked</td>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:600;text-align:right">{categories_tracked}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee">With 2+ snapshots (drop detection ready)</td>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:600;text-align:right">{with_history}</td></tr>
        </table>
    </div>

    <!-- Footer -->
    <div style="padding:16px 24px;background:#f7f8fa;border-top:1px solid #e0e0e0;border-radius:0 0 8px 8px">
        <p style="color:#999;font-size:11px;margin:0">
            LocalRank Sentinel | sutraflow.org | Next run: Monday 1pm UTC<br>
            Runs automatically via GitHub Actions. No action needed unless you see failures.
        </p>
    </div>
</div>"""
        self._send_email(
            f"[LocalRank] {alerts} drops, {emails_sent} teasers sent, {pending_count} awaiting payment",
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
                f"[CRITICAL] LocalRank Sentinel -- {failures} consecutive failures",
                f"""
<div style="font-family:-apple-system,sans-serif;max-width:500px;margin:0 auto">
    <div style="background:#b41e1e;padding:16px 24px;border-radius:8px 8px 0 0">
        <h2 style="color:#fff;margin:0">CRITICAL: Pipeline Failure</h2>
    </div>
    <div style="padding:20px 24px;background:#fff3f3;border:1px solid #fecaca">
        <p style="color:#b41e1e;font-weight:600">{failures} consecutive failures detected</p>
        <p style="color:#666">Latest error:</p>
        <pre style="background:#fff;padding:12px;border:1px solid #e0e0e0;font-size:12px;overflow-x:auto;color:#333">{error[:500]}</pre>
        <p style="color:#666;font-size:13px;margin-top:16px">
            Check GitHub Actions logs:<br>
            <a href="https://github.com/sutrafloworg/zero-touch-business/actions" style="color:#0066cc">
                View workflow runs
            </a>
        </p>
    </div>
</div>""",
            )
