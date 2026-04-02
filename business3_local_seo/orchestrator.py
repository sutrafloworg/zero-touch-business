"""
Local SEO Sentinel Orchestrator — entry point for GitHub Actions weekly run.

Every Monday:
  1. Scanner Agent  → fetch Google Maps rankings via SerpAPI
  2. Analyzer Agent → compare to last week, detect rank drops
  3. Report Agent   → generate personalized audit PDFs with Claude
  4. Outreach Agent → find emails, send audit PDFs to affected businesses
  5. Monitor Agent  → record stats, send summary to owner

Zero-touch after initial setup.
"""
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

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
logger = logging.getLogger("local_seo_orchestrator")

import config
from agents.scanner_agent import ScannerAgent
from agents.analyzer_agent import AnalyzerAgent
from agents.report_agent import ReportAgent
from agents.outreach_agent import OutreachAgent
from agents.monitor_agent import MonitorAgent
from agents.fulfillment_agent import FulfillmentAgent


def _send_subscriber_allclear_emails(
    outreach: OutreachAgent,
    scan_results: dict,
    alerts: list[dict],
    cities_data: list,
) -> int:
    """Send weekly all-clear emails to active subscribers whose ranking didn't drop.

    This ensures subscribers always get a weekly email — either a drop alert
    (handled in process_batch) or an all-clear confirmation here.
    """
    # Load customer registry
    customers_file = Path(__file__).parent / "data" / "customers.json"
    try:
        with open(customers_file) as f:
            customers = json.load(f).get("customers", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return 0

    active_subscribers = [c for c in customers if c["status"] == "active"]
    if not active_subscribers:
        return 0

    # Build set of businesses that DID have a drop (they'll get a different email)
    dropped_businesses = set()
    for alert in alerts:
        dropped_businesses.add(alert["business_name"].lower().strip())

    sent = 0
    for sub in active_subscribers:
        biz_name = sub.get("business_name", "")
        if not biz_name or biz_name.lower().strip() in dropped_businesses:
            continue  # They'll get a drop alert instead — skip all-clear

        # Find current rank from scan results
        cat_key = sub.get("category_key", "")
        current_rank = None
        if cat_key and cat_key in scan_results:
            for biz in scan_results[cat_key]:
                if biz.get("name", "").lower().strip() == biz_name.lower().strip():
                    current_rank = biz.get("rank")
                    break

        if current_rank is None:
            current_rank = 0  # Unknown — still send the email

        # Parse city and category from category_key (e.g. "austin_tx_plumber")
        parts = cat_key.split("_") if cat_key else []
        city = parts[0].title() if parts else "your city"
        category = parts[2].replace("-", " ") if len(parts) > 2 else "your category"

        success = outreach.send_allclear_email(
            to_email=sub["email"],
            business_name=biz_name,
            current_rank=current_rank,
            category=category,
            city=city,
        )
        if success:
            sent += 1

    return sent


def run_pipeline() -> bool:
    logger.info("=" * 60)
    logger.info("Starting Search Sentinel pipeline")
    logger.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)

    if not config.SERPAPI_KEY:
        logger.error("Missing SERPAPI_KEY — cannot scan. Add it to GitHub Secrets.")
        return False

    if not config.ANTHROPIC_API_KEY:
        logger.error("Missing ANTHROPIC_API_KEY — cannot generate reports.")
        return False

    # Load target cities
    try:
        with open(config.CITIES_FILE) as f:
            cities_data = json.load(f)
    except Exception as e:
        logger.error(f"Could not load cities.json: {e}")
        return False

    monitor = MonitorAgent(
        state_file=config.STATE_FILE,
        alert_email=config.ALERT_EMAIL,
        gmail_user=config.GMAIL_USER,
        gmail_app_password=config.GMAIL_APP_PASSWORD,
    )

    try:
        # ── Step 1: Scan ───────────────────────────────────────────────────────
        logger.info("[1/5] Scanner Agent: fetching Google Maps rankings...")
        scanner = ScannerAgent(api_key=config.SERPAPI_KEY)
        scan_results = scanner.scan_all_targets(cities_data)
        total_scans = len(scan_results)
        logger.info(f"      Scanned {total_scans} categories")

        if not scan_results:
            logger.warning("No scan results — exiting")
            return True

        # ── Step 2: Analyze ────────────────────────────────────────────────────
        logger.info("[2/5] Analyzer Agent: detecting rank changes...")
        analyzer = AnalyzerAgent(rankings_file=config.RANKINGS_FILE)
        alerts = analyzer.analyze(scan_results)
        logger.info(f"      Found {len(alerts)} rank drop alerts")

        # ── Step 2b: Send all-clear emails to subscribers with no drops ─────
        # Even if no drops, subscribers need weekly confirmation
        outreach_allclear = OutreachAgent(
            gmail_user=config.GMAIL_USER,
            gmail_app_password=config.GMAIL_APP_PASSWORD,
            payment_url=config.PAYMENT_URL_MONITORING,
            payment_url_audit=config.PAYMENT_URL_AUDIT,
        )
        allclear_sent = _send_subscriber_allclear_emails(
            outreach_allclear, scan_results, alerts, cities_data
        )
        if allclear_sent:
            logger.info(f"      Sent {allclear_sent} all-clear emails to subscribers")

        # ── Step 2c: Deliver any queued reports from earlier payments ──────
        fulfillment_check = FulfillmentAgent(
            index_file=config.PENDING_REPORTS_FILE,
            outreach=outreach_allclear,
        )
        queued_deliveries = fulfillment_check.deliver_queued()
        if queued_deliveries:
            logger.info(f"      Delivered {len(queued_deliveries)} queued reports")

        if not alerts:
            logger.info("No rank drops detected this week — pipeline complete")
            monitor.record_run(total_scans, 0, 0, allclear_sent, {})
            return True

        # ── Step 3: Generate Reports ───────────────────────────────────────────
        logger.info(f"[3/5] Report Agent: generating {len(alerts)} audit PDFs...")
        reporter = ReportAgent(
            api_key=config.ANTHROPIC_API_KEY,
            reports_dir=config.REPORTS_DIR,
            model=config.CLAUDE_MODEL,
        )
        report_results = reporter.generate_batch(alerts)
        logger.info(f"      Generated {len(report_results)} PDFs")

        # ── Step 4: Outreach (teaser emails — no PDF attached) ────────────────
        logger.info("[4/6] Outreach Agent: finding emails + sending teaser alerts...")
        outreach = OutreachAgent(
            gmail_user=config.GMAIL_USER,
            gmail_app_password=config.GMAIL_APP_PASSWORD,
            payment_url=config.PAYMENT_URL_MONITORING,
            payment_url_audit=config.PAYMENT_URL_AUDIT,
        )
        outreach_summary = outreach.process_batch(report_results)

        # ── Step 5: Register reports for post-payment delivery ───────────────
        # Only register non-subscriber contacts (subscribers already got their PDF)
        contacted = outreach_summary.get("contacted", [])
        teaser_contacts = [c for c in contacted if c.get("type") != "subscriber_report"]
        if teaser_contacts:
            logger.info(f"[5/6] Fulfillment: registering {len(teaser_contacts)} reports for delivery...")
            fulfillment = FulfillmentAgent(
                index_file=config.PENDING_REPORTS_FILE,
                outreach=outreach,
            )
            fulfillment.register_reports(teaser_contacts)
        else:
            logger.info("[5/6] Fulfillment: no non-subscriber contacts to register")

        # ── Step 6: Monitor ────────────────────────────────────────────────────
        logger.info("[6/6] Monitor Agent: recording stats...")
        monitor.record_run(
            scans=total_scans,
            alerts=len(alerts),
            reports_generated=len(report_results),
            emails_sent=outreach_summary.get("sent", 0),
            outreach_summary=outreach_summary,
        )

    except Exception as e:
        logger.error(f"Pipeline crashed: {e}", exc_info=True)
        monitor.record_failure(str(e))
        return False

    logger.info("=" * 60)
    logger.info(f"Pipeline complete. {len(alerts)} drops, {outreach_summary.get('sent', 0)} emails sent")
    logger.info("=" * 60)
    return True


if __name__ == "__main__":
    success = run_pipeline()
    sys.exit(0 if success else 1)
