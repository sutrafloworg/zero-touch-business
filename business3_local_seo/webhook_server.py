"""
Stripe Webhook Server — receives payment confirmations and triggers PDF delivery.

Listens for checkout.session.completed events from Stripe.
When a customer pays for an audit report ($10) or monitoring ($5/month),
the fulfillment agent delivers the PDF via email.

Designed to run on Oracle Cloud Always Free tier or Hetzner VPS.

Usage:
    python webhook_server.py                     # production (port 8080)
    FLASK_DEBUG=1 python webhook_server.py       # development

Stripe setup:
    1. Create webhook endpoint in Stripe Dashboard → Developers → Webhooks
    2. URL: https://your-domain.com/stripe/webhook
    3. Events: checkout.session.completed
    4. Copy signing secret → set as STRIPE_WEBHOOK_SECRET env var
"""
import json
import logging
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, request

import stripe

# Add parent dir so we can import agents/config
sys.path.insert(0, str(Path(__file__).parent))

import config
from agents.fulfillment_agent import FulfillmentAgent
from agents.outreach_agent import OutreachAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("webhook_server")

app = Flask(__name__)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# Initialize fulfillment stack
outreach = OutreachAgent(
    gmail_user=config.GMAIL_USER,
    gmail_app_password=config.GMAIL_APP_PASSWORD,
)
fulfillment = FulfillmentAgent(
    index_file=config.PENDING_REPORTS_FILE,
    outreach=outreach,
)


@app.route("/health", methods=["GET"])
def health():
    """Health check for uptime monitoring."""
    stats = fulfillment.get_stats()
    return jsonify({"status": "ok", **stats})


@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    """Handle Stripe webhook events."""
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature", "")

    # Verify webhook signature
    if WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, WEBHOOK_SECRET
            )
        except stripe.SignatureVerificationError:
            logger.warning("Webhook: invalid signature")
            return jsonify({"error": "invalid_signature"}), 400
        except ValueError:
            logger.warning("Webhook: invalid payload")
            return jsonify({"error": "invalid_payload"}), 400
    else:
        # No secret configured — parse without verification (dev only)
        logger.warning("Webhook: no STRIPE_WEBHOOK_SECRET set, skipping verification")
        event = json.loads(payload)

    event_type = event.get("type", "")
    logger.info(f"Webhook: received event {event_type}")

    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        customer_email = session.get("customer_email") or session.get("customer_details", {}).get("email", "")

        if not customer_email:
            logger.warning("Webhook: no customer email in session")
            return jsonify({"error": "no_email"}), 200

        logger.info(f"Webhook: payment received from {customer_email}")

        # Deliver the PDF
        result = fulfillment.deliver(customer_email)

        if result["success"]:
            logger.info(f"Webhook: delivered report {result['report_id']} to {customer_email}")
        else:
            logger.warning(f"Webhook: delivery issue for {customer_email}: {result.get('error')}")

        return jsonify(result), 200

    # Acknowledge other event types without processing
    return jsonify({"received": True}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    if not stripe.api_key:
        logger.warning("STRIPE_SECRET_KEY not set — webhook signature verification disabled")
    if not WEBHOOK_SECRET:
        logger.warning("STRIPE_WEBHOOK_SECRET not set — running without signature verification")

    logger.info(f"Starting webhook server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
