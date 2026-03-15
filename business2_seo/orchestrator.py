"""
SEO Site Orchestrator — entry point for GitHub Actions weekly run.

Execution order:
  1. Keyword Agent  → select next batch of pending keywords
  2. Content Agent  → generate Hugo markdown articles via Claude
  3. Publisher Agent → write files to hugo_site/content/posts/
  4. Monitor Agent  → health checks, self-correction, alerts

GitHub Actions then commits the new .md files and Cloudflare Pages
auto-builds and deploys the Hugo site.
"""
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Logging ────────────────────────────────────────────────────────────────────
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
logger = logging.getLogger("seo_orchestrator")

import config
from agents.keyword_agent import KeywordAgent
from agents.content_agent import ContentAgent
from agents.publisher_agent import PublisherAgent
from agents.monitor_agent import MonitorAgent


def validate_config() -> list[str]:
    missing = []
    if not config.ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    return missing


def run_pipeline() -> bool:
    logger.info("=" * 60)
    logger.info(f"Starting SEO Site pipeline — {config.SITE_NAME}")
    logger.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)

    missing = validate_config()
    if missing:
        logger.error(f"Missing required secrets: {missing}")
        return False

    # ── Initialize agents ──────────────────────────────────────────────────────
    keyword_agent = KeywordAgent(
        keywords_file=config.KEYWORDS_FILE,
        state_file=config.STATE_FILE,
        content_dir=config.CONTENT_OUTPUT_DIR,
    )

    content_agent = ContentAgent(
        api_key=config.ANTHROPIC_API_KEY,
        affiliate_file=config.AFFILIATE_FILE,
        model=config.CLAUDE_MODEL,
        max_retries=config.CLAUDE_RETRY_ATTEMPTS,
        min_word_count=config.MIN_ARTICLE_WORD_COUNT,
    )

    publisher_agent = PublisherAgent(
        content_dir=config.CONTENT_OUTPUT_DIR,
        state_file=config.STATE_FILE,
        site_domain=config.SITE_DOMAIN,
    )

    monitor_agent = MonitorAgent(
        state_file=config.STATE_FILE,
        content_dir=config.CONTENT_OUTPUT_DIR,
        site_domain=config.SITE_DOMAIN,
        alert_email=config.ALERT_EMAIL,
        gmail_user=config.GMAIL_USER,
        gmail_app_password=config.GMAIL_APP_PASSWORD,
    )

    # ── Step 1: Keyword Agent ──────────────────────────────────────────────────
    logger.info(f"[1/4] Keyword Agent: selecting next {config.ARTICLES_PER_RUN} keywords...")
    keywords = keyword_agent.get_next_batch(batch_size=config.ARTICLES_PER_RUN)

    if not keywords:
        logger.info("No pending keywords — pipeline complete (all keywords processed!)")
        stats = keyword_agent.get_stats()
        logger.info(f"Keyword stats: {stats}")
        return True

    logger.info(f"      Selected: {[kw['keyword'] for kw in keywords]}")

    # ── Step 2+3: Content + Publish (per keyword) ──────────────────────────────
    published_slugs = []
    for i, keyword in enumerate(keywords, 1):
        logger.info(f"[2+3/{len(keywords)+2}] Processing: '{keyword['keyword']}' ({i}/{len(keywords)})")

        # Generate article
        article_content, success = content_agent.generate_article(keyword)

        if not success:
            keyword_agent.mark_done(keyword["keyword"], success=False)
            logger.warning(f"Skipping '{keyword['keyword']}' — content generation failed")
            continue

        # Publish (write to disk)
        publish_success = publisher_agent.publish_article(keyword["slug"], article_content)
        keyword_agent.mark_done(keyword["keyword"], success=publish_success)

        if publish_success:
            published_slugs.append(keyword["slug"])

    logger.info(f"      Published {len(published_slugs)}/{len(keywords)} articles this run")

    # Ping Google sitemap
    if published_slugs:
        publisher_agent.ping_google_indexing(published_slugs)
        publisher_agent.update_run_stats(len(published_slugs))

    # ── Step 4: Monitor Agent ──────────────────────────────────────────────────
    logger.info("[4/4] Monitor Agent: health checks...")
    success = monitor_agent.check_and_heal(published_slugs, expected_count=len(keywords))

    # Monthly digest (every 20 articles published)
    from json import load
    try:
        with open(config.STATE_FILE) as f:
            state = load(f)
        if state.get("articles_published", 0) % 20 == 0:
            monitor_agent.send_monthly_digest()
    except Exception:
        pass

    logger.info("=" * 60)
    logger.info(f"Pipeline complete. Published: {len(published_slugs)} articles")
    logger.info("=" * 60)

    return success


if __name__ == "__main__":
    success = run_pipeline()
    sys.exit(0 if success else 1)
