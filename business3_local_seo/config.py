"""
Central configuration for the Local SEO Sentinel business.
All secrets come from environment variables (GitHub Secrets in production).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
REPORTS_DIR = BASE_DIR / "reports"
LOGS_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)

# ── Claude API ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_MAX_TOKENS = 4096
CLAUDE_RETRY_ATTEMPTS = 3

# ── Search API Keys (rotary: SerpAPI → Outscraper → ValueSERP) ───────────────
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
OUTSCRAPER_API_KEY = os.environ.get("OUTSCRAPER_API_KEY", "")
# Note: GitHub Secret is named VALUESERP_KEY (not VALUESERP_API_KEY) — keep this name.
VALUESERP_API_KEY = os.environ.get("VALUESERP_KEY", "") or os.environ.get("VALUESERP_API_KEY", "")

# ── Target Configuration ──────────────────────────────────────────────────────
CITIES_FILE = DATA_DIR / "cities.json"
RANKINGS_FILE = DATA_DIR / "rankings_history.json"
CONTACTS_FILE = DATA_DIR / "contacts.json"
STATE_FILE = DATA_DIR / "state.json"
PENDING_REPORTS_FILE = DATA_DIR / "pending_reports.json"

# ── Email (reuses existing Gmail SMTP) ────────────────────────────────────────
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

# ── Business Settings ─────────────────────────────────────────────────────────
BUSINESS_NAME = "Search Sentinel"
FROM_NAME = "Search Sentinel"
SITE_URL = "https://sutraflow.org"  # cross-promote

# ── Payment (Stripe Payment Links) ───────────────────────────────────────
def _with_utm(base_url: str, source: str, campaign: str, content: str) -> str:
    """Append utm_* params so we can see in Stripe which email/page drove the click."""
    if not base_url:
        return base_url
    sep = "&" if "?" in base_url else "?"
    return (
        f"{base_url}{sep}"
        f"utm_source={source}&utm_medium=email&utm_campaign={campaign}&utm_content={content}"
    )


_BASE_MONITORING = os.environ.get(
    "STRIPE_PAYMENT_URL_MONITORING",
    "https://buy.stripe.com/eVq9AUf253vu36Hg5q6kg01",
)
_BASE_AUDIT = os.environ.get(
    "STRIPE_PAYMENT_URL_AUDIT",
    "https://buy.stripe.com/7sYaEYdY10jifTtdXi6kg00",
)

# Tagged versions for outreach emails (so we can attribute conversions back).
# Stripe Payment Links pass utm_* params through to the success page and
# (when client_reference_id is set) into the checkout session.
PAYMENT_URL_MONITORING = _with_utm(_BASE_MONITORING, "email", "weekly_outreach", "monitor_link")
PAYMENT_URL_AUDIT = _with_utm(_BASE_AUDIT, "email", "weekly_outreach", "audit_link")

# Untagged base versions for landing-page CTAs (the page itself tags them)
PAYMENT_URL_MONITORING_BASE = _BASE_MONITORING
PAYMENT_URL_AUDIT_BASE = _BASE_AUDIT

# ── Stripe (webhook server) ──────────────────────────────────────────────────
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# ── Self-Correction ───────────────────────────────────────────────────────────
MAX_RETRIES = 3
