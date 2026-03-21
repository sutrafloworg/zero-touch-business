"""
Standalone runner for the Stats Agent — sends the weekly report on demand.
Run via GitHub Actions: Actions → "Stats Report" → Run workflow
"""
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

import config
from agents.stats_agent import StatsAgent

agent = StatsAgent(
    kit_api_secret=config.KIT_API_SECRET,
    cf_api_token=config.CF_API_TOKEN,
    cf_account_id=config.CF_ACCOUNT_ID,
    site_domain=config.SITE_DOMAIN,
    state_file=config.STATE_FILE,
    stats_file=config.STATS_HISTORY_FILE,
    alert_email=config.ALERT_EMAIL,
    gmail_user=config.GMAIL_USER,
    gmail_app_password=config.GMAIL_APP_PASSWORD,
)
agent.run_and_report()
print("Done — check your inbox.")
