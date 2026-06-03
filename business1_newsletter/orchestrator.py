"""
Newsletter Orchestrator — the entry point run by GitHub Actions every week.

Execution order:
  1. Feed Agent      → fetch & score RSS articles
  2. Content Agent   → generate newsletter via Claude
  3. Publisher Agent → send via Kit API
  4. Monitor Agent   → health checks, self-correction, alerts

Error philosophy: catch everything, log it, self-correct where possible,
alert the owner only when human intervention is genuinely needed.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Logging Setup ──────────────────────────────────────────────────────────────
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file),
    ],
)
logger = logging.getLogger("orchestrator")

# ── Config & Agents ────────────────────────────────────────────────────────────
import config
from agents.feed_agent import FeedAgent
from agents.content_agent import ContentAgent
from agents.publisher_agent import PublisherAgent
from agents.monitor_agent import MonitorAgent


def validate_config() -> list[str]:
    """Check required environment variables exist."""
    missing = []
    if not config.ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not config.KIT_API_SECRET:
        missing.append("KIT_API_SECRET")
    return missing


def already_ran_this_week(min_days_between_runs: int = 5) -> bool:
    """Return True if the newsletter has been sent in the last `min_days_between_runs` days.

    GitHub Actions cron schedules are unreliable — they can silently delay or skip
    runs during high load. The workflow has a primary trigger on Tuesday plus backup
    triggers on Wed/Thu. This guard ensures we don't send 2-3 newsletters that week
    if multiple backups fire.

    The user can override by setting FORCE=true in the workflow_dispatch inputs.
    """
    if os.environ.get("FORCE", "").lower() in ("true", "1", "yes"):
        logger.info("FORCE env var set — skipping dedup check")
        return False

    try:
        with open(config.STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
        last_run = state.get("last_run")
        if not last_run:
            return False
        last_run_dt = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
        # Make sure it's timezone-aware
        if last_run_dt.tzinfo is None:
            last_run_dt = last_run_dt.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - last_run_dt
        if age < timedelta(days=min_days_between_runs):
            logger.info(
                f"Last run was {age.days}d {age.seconds//3600}h ago — within "
                f"{min_days_between_runs}d guard. Skipping to avoid duplicate send."
            )
            return True
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Dedup check could not read state ({e}) — running anyway")
    return False


def run_pipeline() -> bool:
    """
    Run the complete newsletter pipeline.
    Returns True on success, False on failure.
    """
    logger.info("=" * 60)
    logger.info(f"Starting {config.NEWSLETTER_NAME} pipeline")
    logger.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)

    # ── Weekly dedup guard ─────────────────────────────────────────────────────
    # Backup cron triggers (Wed/Thu) call this same workflow. Skip if we already
    # ran in the past 5 days, unless FORCE=true was set on workflow_dispatch.
    if already_ran_this_week():
        logger.info("Already ran this week — exiting cleanly (this is not a failure)")
        return True

    # ── Pre-flight check ───────────────────────────────────────────────────────
    missing = validate_config()
    if missing:
        logger.error(f"Missing required secrets: {missing}")
        logger.error("Set these in GitHub Actions Secrets and re-run.")
        return False

    # ── Initialize agents ──────────────────────────────────────────────────────
    feed_agent = FeedAgent(
        feeds_file=config.FEEDS_FILE,
        state_file=config.STATE_FILE,
        niche=config.NEWSLETTER_NICHE,
    )

    content_agent = ContentAgent(
        api_key=config.ANTHROPIC_API_KEY,
        affiliate_file=config.AFFILIATE_FILE,
        state_file=config.STATE_FILE,
        newsletter_name=config.NEWSLETTER_NAME,
        niche=config.NEWSLETTER_NICHE,
        tagline=config.NEWSLETTER_TAGLINE,
        model=config.CLAUDE_MODEL,
        max_retries=config.CLAUDE_RETRY_ATTEMPTS,
    )

    publisher_agent = PublisherAgent(
        api_secret=config.KIT_API_SECRET,
        logs_dir=LOG_DIR,
        max_retries=config.MAX_RETRIES,
    )

    monitor_agent = MonitorAgent(
        state_file=config.STATE_FILE,
        logs_dir=LOG_DIR,
        alert_email=config.ALERT_EMAIL,
        gmail_user=config.GMAIL_USER,
        gmail_app_password=config.GMAIL_APP_PASSWORD,
        newsletter_name=config.NEWSLETTER_NAME,
    )

    # ── Step 1: Feed Agent ─────────────────────────────────────────────────────
    logger.info("[1/4] Feed Agent: fetching RSS sources...")
    try:
        feed_items = feed_agent.get_top_items(max_items=config.MAX_FEED_ITEMS_PER_SOURCE * 2)
        if not feed_items:
            logger.error("Feed Agent returned no items — aborting pipeline")
            return False
        logger.info(f"      Got {len(feed_items)} items")
    except Exception as e:
        logger.error(f"Feed Agent crashed: {e}", exc_info=True)
        return False

    # ── Step 2: Content Agent ──────────────────────────────────────────────────
    logger.info("[2/4] Content Agent: generating newsletter via Claude...")
    try:
        subject, preview, html_body = content_agent.generate_issue(
            feed_items[:config.MAX_ITEMS_IN_NEWSLETTER]
        )
        logger.info(f"      Subject: '{subject}'")
    except Exception as e:
        logger.error(f"Content Agent crashed: {e}", exc_info=True)
        return False

    # ── Step 3: Publisher Agent ────────────────────────────────────────────────
    logger.info("[3/4] Publisher Agent: sending via Kit API...")
    try:
        broadcast_id = publisher_agent.publish(subject, preview, html_body)
        subscriber_count = publisher_agent.get_subscriber_count()
        logger.info(f"      Broadcast ID: {broadcast_id}, Subscribers: {subscriber_count}")
    except Exception as e:
        logger.error(f"Publisher Agent crashed: {e}", exc_info=True)
        broadcast_id = "fallback:crash"
        subscriber_count = -1

    # ── Step 4: Monitor Agent ──────────────────────────────────────────────────
    logger.info("[4/4] Monitor Agent: health checks + self-correction...")
    try:
        state = monitor_agent._load_state()
        issues_published = state.get("issues_published", 0)
        if not broadcast_id.startswith("fallback:"):
            issues_published += 1

        success = monitor_agent.check_and_heal(broadcast_id, subscriber_count, issues_published)

        # Send weekly digest every 4th issue
        if issues_published % 4 == 0 and not broadcast_id.startswith("fallback:"):
            monitor_agent.send_weekly_digest(subscriber_count, broadcast_id)

    except Exception as e:
        logger.error(f"Monitor Agent crashed: {e}", exc_info=True)
        success = False

    logger.info("=" * 60)
    logger.info(f"Pipeline complete. Status: {'SUCCESS' if success else 'FAILED'}")
    logger.info("=" * 60)

    return success


if __name__ == "__main__":
    success = run_pipeline()
    sys.exit(0 if success else 1)
