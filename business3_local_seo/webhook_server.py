"""
Stripe Webhook Server — receives payment confirmations and triggers PDF delivery.

Handles the full subscription lifecycle:
  - checkout.session.completed   → first payment / one-time audit delivery
  - invoice.paid                 → recurring monthly payment confirmed, log it
  - invoice.payment_failed       → send payment failure reminder email
  - customer.subscription.deleted → customer cancelled, remove from monitoring

Customer registry (customers.json):
{
  "customers": [
    {
      "email": "owner@business.com",
      "stripe_customer_id": "cus_xxx",
      "stripe_subscription_id": "sub_xxx",
      "plan": "monitor",          # "audit" | "monitor"
      "status": "active",         # "active" | "cancelled" | "past_due"
      "business_name": "Ace Plumbing",
      "category_key": "losangeles_ca_plumber",
      "created_at": "2026-04-01T...",
      "last_payment_at": "2026-04-01T...",
      "cancelled_at": null,
      "payment_failures": 0
    }
  ]
}

PDF reports are generated on-demand when a customer pays (not pre-generated).
The fulfillment agent handles PDF generation + email delivery in one step.

Designed to run on Oracle Cloud Always Free tier or Hetzner VPS.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request

import stripe

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

# Customer registry file
CUSTOMERS_FILE = Path(__file__).parent / "data" / "customers.json"
CUSTOMERS_FILE.parent.mkdir(exist_ok=True)

# Initialize fulfillment stack
outreach = OutreachAgent(
    gmail_user=config.GMAIL_USER,
    gmail_app_password=config.GMAIL_APP_PASSWORD,
)
fulfillment = FulfillmentAgent(
    index_file=config.PENDING_REPORTS_FILE,
    outreach=outreach,
)


# ── Customer Registry ────────────────────────────────────────────────────────

def _load_customers() -> dict:
    try:
        with open(CUSTOMERS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"customers": []}


def _save_customers(data: dict) -> None:
    with open(CUSTOMERS_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)


def _find_customer(data: dict, *, email: str = None, stripe_customer_id: str = None,
                   subscription_id: str = None) -> dict | None:
    for c in data["customers"]:
        if email and c["email"].lower() == email.lower():
            return c
        if stripe_customer_id and c.get("stripe_customer_id") == stripe_customer_id:
            return c
        if subscription_id and c.get("stripe_subscription_id") == subscription_id:
            return c
    return None


def _upsert_customer(data: dict, customer: dict) -> None:
    for i, c in enumerate(data["customers"]):
        if c["email"].lower() == customer["email"].lower():
            data["customers"][i] = customer
            return
    data["customers"].append(customer)


# ── Health Check ─────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    """Health check for uptime monitoring."""
    stats = fulfillment.get_stats()
    data = _load_customers()
    customers = data.get("customers", [])
    stats["active_subscriptions"] = sum(1 for c in customers if c["status"] == "active")
    stats["cancelled_subscriptions"] = sum(1 for c in customers if c["status"] == "cancelled")
    stats["past_due"] = sum(1 for c in customers if c["status"] == "past_due")
    return jsonify({"status": "ok", **stats})


# ── Webhook Router ────────────────────────────────────────────────────────────

@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    """Handle Stripe webhook events."""
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature", "")

    if WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
        except stripe.SignatureVerificationError:
            logger.warning("Webhook: invalid signature")
            return jsonify({"error": "invalid_signature"}), 400
        except ValueError:
            logger.warning("Webhook: invalid payload")
            return jsonify({"error": "invalid_payload"}), 400
    else:
        logger.warning("Webhook: no STRIPE_WEBHOOK_SECRET set, skipping verification")
        event = json.loads(payload)

    event_type = event.get("type", "")
    logger.info(f"Webhook: received event {event_type}")

    handlers = {
        "checkout.session.completed": _handle_checkout_completed,
        "invoice.paid":               _handle_invoice_paid,
        "invoice.payment_failed":     _handle_payment_failed,
        "customer.subscription.deleted": _handle_subscription_cancelled,
    }

    handler = handlers.get(event_type)
    if handler:
        result = handler(event["data"]["object"])
        return jsonify(result), 200

    return jsonify({"received": True, "action": "ignored"}), 200


# ── Event Handlers ────────────────────────────────────────────────────────────

def _handle_checkout_completed(session: dict) -> dict:
    """First payment: register customer and deliver PDF for audits."""
    customer_email = (
        session.get("customer_email")
        or session.get("customer_details", {}).get("email", "")
    )
    if not customer_email:
        logger.warning("Checkout: no customer email")
        return {"error": "no_email"}

    stripe_customer_id = session.get("customer", "")
    subscription_id = session.get("subscription", "")
    mode = session.get("mode", "payment")  # "payment" = one-time, "subscription" = recurring
    plan = "monitor" if mode == "subscription" else "audit"

    # Extract business metadata from Stripe session (set on payment link)
    metadata = session.get("metadata", {})
    business_name = metadata.get("business_name", "")
    category_key = metadata.get("category_key", "")

    # Register / update customer record
    data = _load_customers()
    existing = _find_customer(data, email=customer_email)
    now = datetime.now(timezone.utc).isoformat()

    customer_record = {
        "email": customer_email,
        "stripe_customer_id": stripe_customer_id,
        "stripe_subscription_id": subscription_id,
        "plan": plan,
        "status": "active",
        "business_name": business_name,
        "category_key": category_key,
        "created_at": existing.get("created_at", now) if existing else now,
        "last_payment_at": now,
        "cancelled_at": None,
        "payment_failures": 0,
    }
    _upsert_customer(data, customer_record)
    _save_customers(data)
    logger.info(f"Checkout: registered {plan} customer {customer_email}")

    # Deliver the PDF (for both audit and first monitor payment)
    result = fulfillment.deliver(
        customer_email,
        business_name=business_name,
        category_key=category_key,
    )
    if result["success"]:
        logger.info(f"Checkout: delivered report to {customer_email}")
    elif result.get("error") == "queued_for_generation":
        logger.info(f"Checkout: report queued for {customer_email} — will deliver on next pipeline run")
    else:
        logger.warning(f"Checkout: delivery issue for {customer_email}: {result.get('error')}")

    return {"customer": customer_email, "plan": plan, **result}


def _handle_invoice_paid(invoice: dict) -> dict:
    """Recurring monthly payment confirmed — update last_payment_at and log."""
    stripe_customer_id = invoice.get("customer", "")
    subscription_id = invoice.get("subscription", "")
    amount_paid = invoice.get("amount_paid", 0)  # cents
    period_end = invoice.get("lines", {}).get("data", [{}])[0].get("period", {}).get("end", 0)

    data = _load_customers()
    customer = _find_customer(data, stripe_customer_id=stripe_customer_id,
                               subscription_id=subscription_id)

    if not customer:
        logger.warning(f"Invoice paid: no customer record for stripe_id={stripe_customer_id}")
        return {"error": "customer_not_found"}

    # Validate amount: $5/month = 500 cents
    expected_cents = 500
    if amount_paid != expected_cents:
        logger.warning(
            f"Invoice paid: unexpected amount {amount_paid}c for {customer['email']} "
            f"(expected {expected_cents}c). Logging but not blocking."
        )

    customer["status"] = "active"
    customer["payment_failures"] = 0
    customer["last_payment_at"] = datetime.now(timezone.utc).isoformat()
    _save_customers(data)

    logger.info(
        f"Invoice paid: ${amount_paid/100:.2f} from {customer['email']} "
        f"(period ends {datetime.fromtimestamp(period_end).strftime('%Y-%m-%d') if period_end else 'unknown'})"
    )
    return {"email": customer["email"], "amount_paid": amount_paid, "status": "logged"}


def _handle_payment_failed(invoice: dict) -> dict:
    """Monthly payment failed — mark past_due and send reminder email."""
    stripe_customer_id = invoice.get("customer", "")
    subscription_id = invoice.get("subscription", "")
    attempt_count = invoice.get("attempt_count", 1)

    data = _load_customers()
    customer = _find_customer(data, stripe_customer_id=stripe_customer_id,
                               subscription_id=subscription_id)

    if not customer:
        logger.warning(f"Payment failed: no customer record for stripe_id={stripe_customer_id}")
        return {"error": "customer_not_found"}

    customer["status"] = "past_due"
    customer["payment_failures"] = attempt_count
    _save_customers(data)

    logger.warning(
        f"Payment failed: attempt {attempt_count} for {customer['email']} "
        f"(subscription {subscription_id})"
    )

    # Send payment reminder email
    try:
        _send_payment_reminder(customer, attempt_count)
    except Exception as e:
        logger.error(f"Payment failed: could not send reminder to {customer['email']}: {e}")

    return {"email": customer["email"], "failures": attempt_count, "status": "past_due"}


def _handle_subscription_cancelled(subscription: dict) -> dict:
    """Customer cancelled — mark as cancelled and stop monitoring."""
    stripe_customer_id = subscription.get("customer", "")
    subscription_id = subscription.get("id", "")
    cancel_reason = subscription.get("cancellation_details", {}).get("reason", "unknown")

    data = _load_customers()
    customer = _find_customer(data, stripe_customer_id=stripe_customer_id,
                               subscription_id=subscription_id)

    if not customer:
        logger.warning(f"Cancellation: no customer record for stripe_id={stripe_customer_id}")
        return {"error": "customer_not_found"}

    customer["status"] = "cancelled"
    customer["cancelled_at"] = datetime.now(timezone.utc).isoformat()
    _save_customers(data)

    logger.info(
        f"Cancelled: {customer['email']} (reason: {cancel_reason}). "
        f"Removed from active monitoring."
    )
    return {
        "email": customer["email"],
        "status": "cancelled",
        "reason": cancel_reason,
    }


def _send_payment_reminder(customer: dict, attempt_count: int) -> None:
    """Send a friendly payment failure reminder email."""
    email = customer["email"]
    business_name = customer.get("business_name", "your business")
    subject = f"Action needed: Search Sentinel payment couldn't process"

    if attempt_count == 1:
        body = (
            f"Hi,\n\n"
            f"We weren't able to process your monthly payment for Search Sentinel "
            f"({business_name}). This is usually a temporary card issue.\n\n"
            f"Your monitoring is still active. Stripe will automatically retry in a few days.\n\n"
            f"If your card details have changed, please update them at:\n"
            f"https://billing.stripe.com/p/login/\n\n"
            f"Questions? Reply to this email.\n\n"
            f"— Search Sentinel"
        )
    else:
        body = (
            f"Hi,\n\n"
            f"We've made {attempt_count} attempts to process your Search Sentinel payment "
            f"for {business_name} and haven't been able to complete it.\n\n"
            f"To keep your Google Maps rank monitoring active, please update your payment method:\n"
            f"https://billing.stripe.com/p/login/\n\n"
            f"If we can't process payment, your monitoring will pause automatically.\n\n"
            f"— Search Sentinel"
        )

    outreach._send_email(
        to_email=email,
        subject=subject,
        body_text=body,
    )
    logger.info(f"Reminder sent to {email} (attempt {attempt_count})")


# ── Admin Endpoints ───────────────────────────────────────────────────────────

@app.route("/admin/customers", methods=["GET"])
def list_customers():
    """List all customers with their subscription status."""
    # Simple token check — set ADMIN_TOKEN env var
    token = request.headers.get("X-Admin-Token", "")
    expected = os.environ.get("ADMIN_TOKEN", "")
    if expected and token != expected:
        return jsonify({"error": "unauthorized"}), 401

    data = _load_customers()
    customers = data.get("customers", [])
    summary = {
        "total": len(customers),
        "active": sum(1 for c in customers if c["status"] == "active"),
        "past_due": sum(1 for c in customers if c["status"] == "past_due"),
        "cancelled": sum(1 for c in customers if c["status"] == "cancelled"),
        "customers": customers,
    }
    return jsonify(summary)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    if not stripe.api_key:
        logger.warning("STRIPE_SECRET_KEY not set — webhook signature verification disabled")
    if not WEBHOOK_SECRET:
        logger.warning("STRIPE_WEBHOOK_SECRET not set — running without signature verification")

    logger.info(f"Starting webhook server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
