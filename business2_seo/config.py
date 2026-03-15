"""
Central configuration for the Programmatic SEO Affiliate Site.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
HUGO_SITE_DIR = BASE_DIR / "hugo_site"
LOGS_DIR.mkdir(exist_ok=True)

# ── Claude API ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-haiku-4-5-20251001"   # ~$0.02 per article
CLAUDE_MAX_TOKENS = 3000
CLAUDE_RETRY_ATTEMPTS = 3

# ── Site Configuration ─────────────────────────────────────────────────────────
SITE_NAME = "AI Tools Insider"
SITE_DOMAIN = os.environ.get("SITE_DOMAIN", "your-domain.com")
SITE_DESCRIPTION = "Honest AI tool comparisons, reviews, and guides for professionals"
AUTHOR_NAME = "AI Tools Insider Team"

# ── GitHub Publishing ──────────────────────────────────────────────────────────
# In GitHub Actions, GITHUB_TOKEN is auto-provided with write permissions
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY", "")    # e.g., "username/aitoolsinsider"

# ── Content Generation ─────────────────────────────────────────────────────────
ARTICLES_PER_RUN = int(os.environ.get("ARTICLES_PER_RUN", "5"))
KEYWORDS_FILE = DATA_DIR / "keywords.csv"
STATE_FILE = DATA_DIR / "state.json"
AFFILIATE_FILE = DATA_DIR / "affiliate_links.json"
CONTENT_OUTPUT_DIR = HUGO_SITE_DIR / "content" / "posts"
CONTENT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Alert Settings ─────────────────────────────────────────────────────────────
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

# ── Self-Correction ────────────────────────────────────────────────────────────
MAX_RETRIES = 3
BACKOFF_BASE = 2
MIN_ARTICLE_WORD_COUNT = 600   # reject articles shorter than this
