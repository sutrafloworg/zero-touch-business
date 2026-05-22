"""
Daily quota check — emails the owner if any search-API provider is >80% used
and we're still more than 5 days from the billing-period reset.

Designed to run from GitHub Actions on a daily cron. Cheap, fast, no API calls.
"""
import logging
import smtplib
import sys
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from pathlib import Path

import config
from agents.json_store import safe_load_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

USAGE_FILE = Path(__file__).parent / "data" / "search_usage.json"

# Limits must match scanner_agent.py defaults.
LIMITS = {
    "serpapi": 245,
    "outscraper": 95,
    "valueserp": 95,
}

WARN_PCT = 0.80
RESET_DAY = 22  # SerpAPI billing anniversary
DAYS_THRESHOLD = 5  # only warn if reset is more than this many days away


def _days_until_reset(today: datetime) -> int:
    """Days until the next SerpAPI billing reset (next 22nd of the month)."""
    if today.day < RESET_DAY:
        # Reset is later this month
        reset = today.replace(day=RESET_DAY)
    else:
        # Reset is next month's 22nd
        year = today.year
        month = today.month + 1
        if month > 12:
            month = 1
            year += 1
        reset = today.replace(year=year, month=month, day=RESET_DAY)
    return (reset.date() - today.date()).days


def check_and_alert() -> dict:
    usage = safe_load_json(USAGE_FILE, {})
    today = datetime.now(timezone.utc)
    days_left = _days_until_reset(today)

    warnings = []
    for provider, limit in LIMITS.items():
        used = usage.get(provider, 0)
        pct = used / limit if limit else 0
        if pct >= WARN_PCT:
            warnings.append({
                "provider": provider,
                "used": used,
                "limit": limit,
                "pct": round(pct * 100, 1),
            })

    if not warnings:
        logger.info(f"Quota OK — {days_left}d until reset. Usage: {usage}")
        return {"warnings": 0, "days_until_reset": days_left}

    if days_left <= DAYS_THRESHOLD:
        logger.info(f"Quota high but only {days_left}d until reset — not alerting")
        return {"warnings": len(warnings), "days_until_reset": days_left, "alerted": False}

    # Send alert email
    alert_email = config.ALERT_EMAIL
    if not alert_email or not config.GMAIL_USER or not config.GMAIL_APP_PASSWORD:
        logger.error("Quota warning: email creds not configured")
        return {"warnings": len(warnings), "alerted": False, "error": "no_email_config"}

    lines = [
        f"Search Sentinel quota warning — {today.strftime('%Y-%m-%d')}",
        f"Billing period resets in {days_left} days.",
        "",
        "Providers >= 80% used:",
    ]
    for w in warnings:
        lines.append(f"  - {w['provider']}: {w['used']}/{w['limit']} ({w['pct']}%)")
    lines.append("")
    lines.append("Full usage snapshot:")
    for k, v in usage.items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("If you don't intervene, scans may degrade or skip categories.")
    body = "\n".join(lines)

    msg = MIMEText(body, "plain")
    msg["Subject"] = f"⚠️  Search Sentinel: quota >= 80% ({days_left}d to reset)"
    msg["From"] = f"Search Sentinel <{config.GMAIL_USER}>"
    msg["To"] = alert_email
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
            server.sendmail(config.GMAIL_USER, [alert_email], msg.as_string())
        logger.info(f"Quota warning sent to {alert_email}: {warnings}")
        return {"warnings": len(warnings), "alerted": True, "days_until_reset": days_left}
    except Exception as e:
        logger.error(f"Quota warning email failed: {e}")
        return {"warnings": len(warnings), "alerted": False, "error": str(e)}


if __name__ == "__main__":
    result = check_and_alert()
    print(f"Result: {result}")
    # Exit 0 even on warnings — this is informational, not a build failure.
    sys.exit(0)
