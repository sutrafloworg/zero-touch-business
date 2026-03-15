"""
Central configuration for the Newsletter Automation Business.
All secrets come from environment variables (GitHub Secrets in production).
"""
import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# ── Claude API ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Use Haiku for cost efficiency: ~$0.015 per newsletter issue
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_MAX_TOKENS = 4096
CLAUDE_RETRY_ATTEMPTS = 3
CLAUDE_RETRY_DELAY = 5  # seconds, doubles each retry

# ── Newsletter Settings ────────────────────────────────────────────────────────
NEWSLETTER_NAME = "AI Tools Weekly"
NEWSLETTER_NICHE = "AI productivity tools for solopreneurs and creators"
NEWSLETTER_TAGLINE = "The 5-minute AI briefing that saves you 5 hours"

# ConvertKit (Kit) API — free up to 10,000 subscribers
KIT_API_SECRET = os.environ.get("KIT_API_SECRET", "")
KIT_API_KEY = os.environ.get("KIT_API_KEY", "")       # public key (for forms)
KIT_FORM_ID = os.environ.get("KIT_FORM_ID", "")       # landing page form ID

# ── Email Alerts (Gmail SMTP for error notifications) ─────────────────────────
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")        # where alerts go
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

# ── RSS Feed Sources ───────────────────────────────────────────────────────────
FEEDS_FILE = DATA_DIR / "feeds.json"
AFFILIATE_FILE = DATA_DIR / "affiliate_links.json"
STATE_FILE = DATA_DIR / "state.json"

# ── Content Generation ─────────────────────────────────────────────────────────
MAX_FEED_ITEMS_PER_SOURCE = 5   # articles pulled per RSS source
MAX_ITEMS_IN_NEWSLETTER = 6     # stories featured per issue
NEWSLETTER_WORD_TARGET = 600    # approximate word count per issue

# ── Self-Correction ────────────────────────────────────────────────────────────
MAX_RETRIES = 3
BACKOFF_BASE = 2   # exponential backoff multiplier
CRITICAL_FAILURE_THRESHOLD = 3  # alert after this many consecutive failures
