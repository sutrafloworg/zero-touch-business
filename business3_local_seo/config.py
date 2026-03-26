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

# ── SerpAPI (free tier: 100 searches/month) ────────────────────────────────────
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")

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
BUSINESS_NAME = "LocalRank Sentinel"
FROM_NAME = "LocalRank Sentinel"
SITE_URL = "https://sutraflow.org"  # cross-promote

# ── Payment (Stripe Payment Links) ───────────────────────────────────────
PAYMENT_URL_MONITORING = os.environ.get(
    "STRIPE_PAYMENT_URL_MONITORING",
    "https://buy.stripe.com/eVq9AUf253vu36Hg5q6kg01",
)
PAYMENT_URL_AUDIT = os.environ.get(
    "STRIPE_PAYMENT_URL_AUDIT",
    "https://buy.stripe.com/7sYaEYdY10jifTtdXi6kg00",
)

# ── Stripe (webhook server) ──────────────────────────────────────────────────
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# ── Self-Correction ───────────────────────────────────────────────────────────
MAX_RETRIES = 3
