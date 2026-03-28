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
from agents.stats_agent import StatsAgent
from agents.quality_agent import QualityAgent
from agents.internal_linker import InternalLinker


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

    # ── Step 2-4: Generate → Score → Publish (per keyword) ─────────────────────
    quality_agent = QualityAgent(
        api_key=config.ANTHROPIC_API_KEY,
        threshold=config.QUALITY_THRESHOLD,
        log_file=config.QUALITY_LOG_FILE,
        model=config.CLAUDE_MODEL,
    )

    internal_linker = InternalLinker(content_dir=config.CONTENT_OUTPUT_DIR)

    published_slugs = []
    quality_stats = {
        "articles_generated": 0,
        "articles_passed": 0,
        "articles_revised": 0,
        "articles_rejected": 0,
        "all_scores": [],
        "rejected_keywords": [],
    }

    for i, keyword in enumerate(keywords, 1):
        logger.info(f"[2-4/{len(keywords)+2}] Processing: '{keyword['keyword']}' ({i}/{len(keywords)})")

        # Step 2: Generate article
        article_content, success = content_agent.generate_article(keyword)

        if not success:
            keyword_agent.mark_done(keyword["keyword"], success=False)
            logger.warning(f"Skipping '{keyword['keyword']}' — content generation failed")
            continue

        quality_stats["articles_generated"] += 1

        # Step 3: Quality gate
        score_result = quality_agent.score_article(article_content, keyword)
        quality_stats["all_scores"].append(score_result["scores"])

        if not score_result["passed"]:
            # One revision attempt
            logger.info(
                f"Quality gate: '{keyword['keyword']}' failed "
                f"(lowest: {score_result['lowest_criteria']}). Revising..."
            )
            revised_content, rev_success = content_agent.revise_article(
                article_content, keyword, score_result
            )

            if rev_success:
                quality_stats["articles_revised"] += 1
                # Re-score revised article
                score_result = quality_agent.score_article(revised_content, keyword)
                quality_stats["all_scores"].append(score_result["scores"])

                if score_result["passed"]:
                    article_content = revised_content
                    logger.info(f"Quality gate: '{keyword['keyword']}' passed after revision")
                else:
                    # Still failing — reject
                    quality_stats["articles_rejected"] += 1
                    quality_stats["rejected_keywords"].append(keyword["keyword"])
                    keyword_agent.mark_done(keyword["keyword"], success=False)
                    logger.warning(
                        f"Quality gate: '{keyword['keyword']}' rejected after revision "
                        f"(lowest: {score_result['lowest_criteria']})"
                    )
                    continue
            else:
                # Revision itself failed — reject
                quality_stats["articles_rejected"] += 1
                quality_stats["rejected_keywords"].append(keyword["keyword"])
                keyword_agent.mark_done(keyword["keyword"], success=False)
                logger.warning(f"Quality gate: revision failed for '{keyword['keyword']}'")
                continue

        quality_stats["articles_passed"] += 1

        # Step 4: Internal linking (add cross-links to related articles)
        article_content = internal_linker.add_internal_links(article_content, keyword["slug"])

        # Step 5: Publish (write to disk)
        publish_success = publisher_agent.publish_article(keyword["slug"], article_content)
        keyword_agent.mark_done(keyword["keyword"], success=publish_success)

        if publish_success:
            published_slugs.append(keyword["slug"])

    logger.info(
        f"      Published {len(published_slugs)}/{len(keywords)} articles "
        f"(revised={quality_stats['articles_revised']}, rejected={quality_stats['articles_rejected']})"
    )

    # Log quality metrics
    if quality_stats["all_scores"]:
        avg_scores = {}
        for key in ("structure", "eeat", "seo", "readability", "affiliate", "originality"):
            vals = [s.get(key, 0) for s in quality_stats["all_scores"]]
            avg_scores[key] = round(sum(vals) / len(vals), 1)
        quality_agent.log_run({
            "articles_generated": quality_stats["articles_generated"],
            "articles_passed": quality_stats["articles_passed"],
            "articles_revised": quality_stats["articles_revised"],
            "articles_rejected": quality_stats["articles_rejected"],
            "avg_scores": avg_scores,
            "rejected_keywords": quality_stats["rejected_keywords"],
        })

    # Ping Google sitemap
    if published_slugs:
        publisher_agent.ping_google_indexing(published_slugs)
        publisher_agent.update_run_stats(len(published_slugs))

    # ── Step 4: Monitor Agent ──────────────────────────────────────────────────
    logger.info("[4/4] Monitor Agent: health checks...")
    success = monitor_agent.check_and_heal(published_slugs, expected_count=len(keywords))

    # ── Step 5: Stats Agent ────────────────────────────────────────────────────
    logger.info("[5/5] Stats Agent: collecting metrics + sending weekly report...")
    try:
        stats_agent = StatsAgent(
            kit_api_secret=config.KIT_API_SECRET,
            cf_api_token=config.CF_API_TOKEN,
            cf_account_id=config.CF_ACCOUNT_ID,
            site_domain=config.SITE_DOMAIN,
            state_file=config.STATE_FILE,
            stats_file=config.STATS_HISTORY_FILE,
            alert_email=config.ALERT_EMAIL,
            gmail_user=config.GMAIL_USER,
            gmail_app_password=config.GMAIL_APP_PASSWORD,
            local_seo_state_file=config.LOCAL_SEO_STATE_FILE,
        )
        stats_agent.run_and_report()
    except Exception as e:
        logger.error(f"Stats Agent failed (non-critical): {e}")

    logger.info("=" * 60)
    logger.info(f"Pipeline complete. Published: {len(published_slugs)} articles")
    logger.info("=" * 60)

    return success


if __name__ == "__main__":
    success = run_pipeline()
    sys.exit(0 if success else 1)
