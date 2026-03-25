"""
Stats Agent — collects metrics from Kit API and Cloudflare Analytics,
saves a weekly snapshot, and sends an HTML digest email every Sunday.

Data sources (all use existing GitHub Secrets — no new credentials needed):
  - Kit API v3:          subscriber count + latest broadcast open/click rates
  - Cloudflare GraphQL:  weekly page views for sutraflow.org
  - local state.json:    articles published, keywords processed

Self-correction: any individual data source failure is caught and reported
as "N/A" — the report still sends with partial data.
"""
import json
import logging
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

KIT_BASE_URL = "https://api.convertkit.com/v3"
CF_GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"


class StatsAgent:
    def __init__(
        self,
        kit_api_secret: str,
        cf_api_token: str,
        cf_account_id: str,
        site_domain: str,
        state_file: Path,
        stats_file: Path,
        alert_email: str,
        gmail_user: str,
        gmail_app_password: str,
        newsletter_name: str = "AI Tools Weekly",
        local_seo_state_file: Path | None = None,
    ):
        self.kit_api_secret = kit_api_secret
        self.cf_api_token = cf_api_token
        self.cf_account_id = cf_account_id
        self.site_domain = site_domain
        self.state_file = state_file
        self.stats_file = stats_file
        self.alert_email = alert_email
        self.gmail_user = gmail_user
        self.gmail_app_password = gmail_app_password
        self.newsletter_name = newsletter_name
        self.local_seo_state_file = local_seo_state_file

    # ── Data Collection ────────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except Exception:
            return {}

    def _load_history(self) -> list:
        try:
            with open(self.stats_file) as f:
                return json.load(f)
        except Exception:
            return []

    def _save_history(self, history: list) -> None:
        with open(self.stats_file, "w") as f:
            json.dump(history, f, indent=2, default=str)

    def _get_kit_stats(self) -> dict:
        """Fetch subscriber count and latest broadcast stats from Kit API."""
        try:
            # Subscriber count
            resp = requests.get(
                f"{KIT_BASE_URL}/subscribers",
                params={"api_secret": self.kit_api_secret, "sort_field": "created_at"},
                timeout=15,
            )
            resp.raise_for_status()
            subscriber_count = resp.json().get("total_subscribers", 0)

            # Latest broadcast stats
            broadcasts_resp = requests.get(
                f"{KIT_BASE_URL}/broadcasts",
                params={"api_secret": self.kit_api_secret},
                timeout=15,
            )
            broadcasts_resp.raise_for_status()
            broadcasts = broadcasts_resp.json().get("broadcasts", [])

            open_rate = click_rate = recipients = None
            last_subject = None

            if broadcasts:
                latest = broadcasts[0]
                broadcast_id = latest.get("id")
                last_subject = latest.get("subject", "")

                stats_resp = requests.get(
                    f"{KIT_BASE_URL}/broadcasts/{broadcast_id}/stats",
                    params={"api_secret": self.kit_api_secret},
                    timeout=15,
                )
                if stats_resp.status_code == 200:
                    stats = stats_resp.json().get("broadcast", {})
                    open_rate = round(stats.get("open_rate", 0) * 100, 1)
                    click_rate = round(stats.get("click_rate", 0) * 100, 1)
                    recipients = stats.get("recipients", 0)

            return {
                "subscriber_count": subscriber_count,
                "open_rate": open_rate,
                "click_rate": click_rate,
                "recipients": recipients,
                "last_subject": last_subject,
            }
        except Exception as e:
            logger.warning(f"Kit stats fetch failed: {e}")
            return {}

    def _get_cf_analytics(self) -> dict:
        """Fetch weekly page views from Cloudflare Analytics GraphQL API."""
        if not self.cf_api_token or not self.cf_account_id:
            return {}

        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")
        end = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Try httpRequestsAdaptiveGroups (zone-based) — fallback gracefully
        query = """
        query ($accountTag: string!, $start: string!, $end: string!) {
          viewer {
            accounts(filter: { accountTag: $accountTag }) {
              httpRequestsAdaptiveGroups(
                filter: {
                  datetime_geq: $start
                  datetime_leq: $end
                  requestSource: "eyeball"
                }
                limit: 10
                orderBy: [count_DESC]
              ) {
                count
                dimensions {
                  clientRequestPath
                }
              }
            }
          }
        }
        """

        try:
            resp = requests.post(
                CF_GRAPHQL_URL,
                headers={
                    "Authorization": f"Bearer {self.cf_api_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "variables": {
                        "accountTag": self.cf_account_id,
                        "start": start,
                        "end": end,
                    },
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()

            groups = (
                data.get("data", {})
                .get("viewer", {})
                .get("accounts", [{}])[0]
                .get("httpRequestsAdaptiveGroups", [])
            )

            total_views = sum(g.get("count", 0) for g in groups)
            # Filter to actual page paths (not assets)
            top_pages = [
                {"path": g["dimensions"]["clientRequestPath"], "views": g["count"]}
                for g in groups
                if g["dimensions"].get("clientRequestPath", "").startswith("/posts/")
            ][:5]

            return {"total_views": total_views, "top_pages": top_pages}

        except Exception as e:
            logger.warning(f"Cloudflare analytics fetch failed: {e}")
            return {}

    def _get_local_seo_stats(self) -> dict:
        """Load Business 3 (LocalRank Sentinel) state file for report."""
        if not self.local_seo_state_file:
            return {}
        try:
            with open(self.local_seo_state_file) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Local SEO state fetch failed: {e}")
            return {}

    # ── Report Building ────────────────────────────────────────────────────────

    def _build_html_report(
        self,
        kit: dict,
        cf: dict,
        seo_state: dict,
        prev_snapshot: dict,
        local_seo: dict | None = None,
    ) -> tuple[str, str]:
        """Build HTML email report. Returns (subject, html_body)."""
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%b %d, %Y")

        # Week-over-week subscriber delta
        prev_subs = prev_snapshot.get("subscriber_count", 0)
        curr_subs = kit.get("subscriber_count", 0)
        sub_delta = curr_subs - prev_subs if prev_subs else 0
        sub_delta_str = f"+{sub_delta}" if sub_delta >= 0 else str(sub_delta)

        def fmt(val, suffix=""):
            return f"{val}{suffix}" if val is not None else "N/A"

        # Top pages list
        top_pages_html = ""
        if cf.get("top_pages"):
            rows = "".join(
                f"<tr><td style='padding:3px 8px;color:#555;font-size:12px'>{p['path']}</td>"
                f"<td style='padding:3px 8px;text-align:right;color:#333;font-size:12px'>{p['views']:,}</td></tr>"
                for p in cf["top_pages"]
            )
            top_pages_html = f"""
            <table style='width:100%;border-collapse:collapse;margin-top:8px'>
              <tr><th style='text-align:left;font-size:11px;color:#888;padding:3px 8px'>Page</th>
                  <th style='text-align:right;font-size:11px;color:#888;padding:3px 8px'>Views</th></tr>
              {rows}
            </table>"""

        articles_live = seo_state.get("articles_published", 0)
        new_articles = seo_state.get("published_this_run", [])

        subject = f"📊 Weekly Report — {self.newsletter_name} [{date_str}]"

        html_body = f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:560px;margin:0 auto;color:#1a1a1a">

  <div style="background:#0f0f0f;padding:20px 24px;border-radius:6px 6px 0 0">
    <h1 style="color:#fff;font-size:16px;margin:0;letter-spacing:.5px">📊 Weekly Business Report</h1>
    <p style="color:#888;font-size:12px;margin:4px 0 0">{date_str} · {self.newsletter_name}</p>
  </div>

  <!-- Newsletter -->
  <div style="padding:20px 24px;background:#fafafa;border:1px solid #e5e5e5;border-top:none">
    <h2 style="font-size:13px;text-transform:uppercase;letter-spacing:1px;color:#888;margin:0 0 12px">Newsletter</h2>
    <table style="width:100%;border-collapse:collapse">
      <tr>
        <td style="padding:6px 0;color:#555;font-size:14px">Subscribers</td>
        <td style="padding:6px 0;font-size:14px;font-weight:600;text-align:right">
          {fmt(curr_subs)}
          <span style="font-size:12px;color:{'#22c55e' if sub_delta >= 0 else '#ef4444'};margin-left:6px">{sub_delta_str} this week</span>
        </td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#555;font-size:14px">Open rate (last issue)</td>
        <td style="padding:6px 0;font-size:14px;font-weight:600;text-align:right">{fmt(kit.get('open_rate'), '%')}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#555;font-size:14px">Click rate</td>
        <td style="padding:6px 0;font-size:14px;font-weight:600;text-align:right">{fmt(kit.get('click_rate'), '%')}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#555;font-size:14px">Recipients (last send)</td>
        <td style="padding:6px 0;font-size:14px;font-weight:600;text-align:right">{fmt(kit.get('recipients'))}</td>
      </tr>
    </table>
    <p style="margin:12px 0 0;font-size:12px;color:#999">
      Last subject: <em>{kit.get('last_subject', 'N/A')}</em><br>
      <a href="https://app.kit.com/broadcasts" style="color:#0066cc">View all broadcasts →</a>
    </p>
  </div>

  <!-- SEO Site -->
  <div style="padding:20px 24px;background:#fff;border:1px solid #e5e5e5;border-top:none">
    <h2 style="font-size:13px;text-transform:uppercase;letter-spacing:1px;color:#888;margin:0 0 12px">SEO Site · sutraflow.org</h2>
    <table style="width:100%;border-collapse:collapse">
      <tr>
        <td style="padding:6px 0;color:#555;font-size:14px">Page views (7 days)</td>
        <td style="padding:6px 0;font-size:14px;font-weight:600;text-align:right">{f"{cf.get('total_views', 0):,}" if cf.get('total_views') is not None else 'N/A'}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#555;font-size:14px">Articles live</td>
        <td style="padding:6px 0;font-size:14px;font-weight:600;text-align:right">{articles_live}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#555;font-size:14px">Published this run</td>
        <td style="padding:6px 0;font-size:14px;font-weight:600;text-align:right">{len(new_articles)}</td>
      </tr>
    </table>
    {top_pages_html}
    <p style="margin:12px 0 0;font-size:12px;color:#999">
      <a href="https://search.google.com/search-console" style="color:#0066cc">Google Search Console →</a> ·
      <a href="https://sutraflow.org" style="color:#0066cc">View site →</a>
    </p>
  </div>

  <!-- Local SEO Sentinel -->
  <div style="padding:20px 24px;background:#fafafa;border:1px solid #e5e5e5;border-top:none">
    <h2 style="font-size:13px;text-transform:uppercase;letter-spacing:1px;color:#888;margin:0 0 12px">Business 3 · LocalRank Sentinel</h2>
    <table style="width:100%;border-collapse:collapse">
      <tr>
        <td style="padding:6px 0;color:#555;font-size:14px">Total pipeline runs</td>
        <td style="padding:6px 0;font-size:14px;font-weight:600;text-align:right">{(local_seo or {}).get('total_runs', 0)}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#555;font-size:14px">Last run status</td>
        <td style="padding:6px 0;font-size:14px;font-weight:600;text-align:right">{(local_seo or {}).get('last_status', 'NOT_RUN')}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#555;font-size:14px">Audit emails sent (all time)</td>
        <td style="padding:6px 0;font-size:14px;font-weight:600;text-align:right">{(local_seo or {}).get('total_emails_sent', 0)}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#555;font-size:14px">Audit PDFs generated (all time)</td>
        <td style="padding:6px 0;font-size:14px;font-weight:600;text-align:right">{(local_seo or {}).get('total_reports_generated', 0)}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#555;font-size:14px">Last alerts detected</td>
        <td style="padding:6px 0;font-size:14px;font-weight:600;text-align:right">{(local_seo or {}).get('last_alerts', 0)}</td>
      </tr>
    </table>
    <p style="margin:12px 0 0;font-size:12px;color:#999">
      Runs every Monday 8am ET · Monitors Google Maps rankings in target cities
    </p>
  </div>

  <!-- Affiliate -->
  <div style="padding:20px 24px;background:#fff;border:1px solid #e5e5e5;border-top:none">
    <h2 style="font-size:13px;text-transform:uppercase;letter-spacing:1px;color:#888;margin:0 0 12px">Affiliate Revenue Dashboards</h2>
    <ul style="margin:0;padding:0 0 0 16px;font-size:13px;color:#555;line-height:2">
      <li><a href="https://app.partnerstack.com" style="color:#0066cc">Copy.ai + Surfer SEO + Writesonic → PartnerStack</a></li>
      <li><a href="https://rytr.me/affiliate" style="color:#0066cc">Rytr → Affiliate dashboard</a></li>
      <li><a href="https://www.notion.so/affiliates" style="color:#0066cc">Notion → Direct dashboard (paused — check periodically)</a></li>
    </ul>
  </div>

  <div style="padding:12px 24px;background:#f0f0f0;border:1px solid #e5e5e5;border-top:none;font-size:11px;color:#999;text-align:center">
    Auto-generated by Stats Agent · {self.newsletter_name} · sutraflow.org
  </div>

</div>""".strip()

        return subject, html_body

    # ── Delivery ───────────────────────────────────────────────────────────────

    def _send_email(self, subject: str, html_body: str) -> bool:
        if not all([self.gmail_user, self.gmail_app_password, self.alert_email]):
            logger.warning("Email not configured — skipping stats report")
            return False
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.newsletter_name} Stats <{self.gmail_user}>"
            msg["To"] = self.alert_email
            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self.gmail_user, self.gmail_app_password)
                server.sendmail(self.gmail_user, [self.alert_email], msg.as_string())

            logger.info(f"Stats report sent to {self.alert_email}")
            return True
        except Exception as e:
            logger.error(f"Failed to send stats report: {e}")
            return False

    # ── Public Interface ────────────────────────────────────────────────────────

    def run_and_report(self) -> None:
        """Collect stats, save snapshot, send weekly HTML report."""
        logger.info("Stats Agent: collecting weekly metrics...")

        kit = self._get_kit_stats()
        cf = self._get_cf_analytics()
        seo_state = self._load_state()
        local_seo = self._get_local_seo_stats()
        history = self._load_history()

        # Previous snapshot for delta calculation
        prev = history[-1] if history else {}

        # Save snapshot
        snapshot = {
            "date": datetime.now(timezone.utc).isoformat(),
            "subscriber_count": kit.get("subscriber_count", 0),
            "open_rate": kit.get("open_rate"),
            "click_rate": kit.get("click_rate"),
            "total_page_views_7d": cf.get("total_views", 0),
            "articles_published": seo_state.get("articles_published", 0),
            "local_seo_runs": local_seo.get("total_runs", 0),
            "local_seo_emails_sent": local_seo.get("total_emails_sent", 0),
        }
        history.append(snapshot)
        self._save_history(history)
        logger.info(f"Stats Agent: snapshot saved ({len(history)} total entries)")

        # Build and send report
        subject, html_body = self._build_html_report(kit, cf, seo_state, prev, local_seo)
        self._send_email(subject, html_body)
