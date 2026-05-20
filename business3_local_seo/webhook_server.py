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


# ── CORS ─────────────────────────────────────────────────────────────────────
_CORS_ORIGINS = {"https://sutraflow.org", "https://www.sutraflow.org"}


@app.after_request
def add_cors(response):
    origin = request.headers.get("Origin", "")
    if origin in _CORS_ORIGINS or not origin:
        response.headers["Access-Control-Allow-Origin"] = origin if origin else "*"
    else:
        response.headers["Access-Control-Allow-Origin"] = "https://sutraflow.org"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, X-Admin-Token, Stripe-Signature, Authorization"
    )
    response.headers["Access-Control-Max-Age"] = "86400"
    return response


@app.route("/health", methods=["OPTIONS"])
@app.route("/admin/customers", methods=["OPTIONS"])
@app.route("/admin/alerts", methods=["OPTIONS"])
@app.route("/admin/budget", methods=["OPTIONS"])
@app.route("/admin/heatmap", methods=["OPTIONS"])
@app.route("/testimonial", methods=["OPTIONS"])
@app.route("/testimonials/public", methods=["OPTIONS"])
def cors_preflight():
    return "", 204


# ─────────────────────────────────────────────────────────────────────────────

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
    """List all customers and general pipeline funnel metrics."""
    token = request.headers.get("X-Admin-Token", "")
    expected = os.environ.get("ADMIN_TOKEN", "")
    if expected and token != expected:
        return jsonify({"error": "unauthorized"}), 401

    data = _load_customers()
    customers = data.get("customers", [])
    
    # Calculate conversion funnel metrics
    pending_data = load_json_safe(config.PENDING_REPORTS_FILE, {"reports": []})
    reports = pending_data.get("reports", [])
    total_leads_contacted = len(reports)
    
    summary = {
        "pipeline": {
            "total_leads_emailed": total_leads_contacted,
            "converted_customers": len(customers),
            "conversion_rate_pct": round((len(customers) / max(1, total_leads_contacted)) * 100, 1),
            "monthly_recurring_revenue": sum(5 for c in customers if c["status"] == "active" and c["plan"] == "monitor"),
            "one_time_revenue": sum(10 for c in customers if c["plan"] == "audit")
        },
        "total": len(customers),
        "active": sum(1 for c in customers if c["status"] == "active"),
        "past_due": sum(1 for c in customers if c["status"] == "past_due"),
        "cancelled": sum(1 for c in customers if c["status"] == "cancelled"),
        "customers": customers,
    }
    return jsonify(summary)

def load_json_safe(path: Path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}

@app.route("/admin/alerts", methods=["GET"])
def get_recent_alerts():
    """Returns the most recent rank drop alerts sent out to leads."""
    token = request.headers.get("X-Admin-Token", "")
    expected = os.environ.get("ADMIN_TOKEN", "")
    if expected and token != expected:
        return jsonify({"error": "unauthorized"}), 401

    pending_data = load_json_safe(config.PENDING_REPORTS_FILE, {"reports": []})
    reports = pending_data.get("reports", [])
    
    # Sort by created_at descending and grab top 10
    recent = sorted(reports, key=lambda x: x.get("created_at", ""), reverse=True)[:10]
    
    return jsonify({
        "total_alerts_tracked": len(reports),
        "recent_alerts": recent
    })

@app.route("/admin/budget", methods=["GET"])
def get_budget():
    """Returns the API usage budget for SerpAPI, Outscraper, etc."""
    token = request.headers.get("X-Admin-Token", "")
    expected = os.environ.get("ADMIN_TOKEN", "")
    if expected and token != expected:
        return jsonify({"error": "unauthorized"}), 401

    usage_file = Path(__file__).parent / "data" / "search_usage.json"
    usage = load_json_safe(usage_file, {
        "month": datetime.now(timezone.utc).strftime("%Y-%m"),
        "serpapi": 0,
        "valueserp": 0,
        "outscraper": 0
    })
    
    # Send budget limits down to frontend
    usage["limits"] = {
        "serpapi": 245,     # SerpAPI free tier is usually 100-250
        "valueserp": 95, 
        "outscraper": 500   # Outscraper gives $2 monthly free
    }
    return jsonify(usage)
    
@app.route("/admin/heatmap", methods=["GET"])
def get_heatmap():
    """Stubbed endpoint for geographic ranking multi-point grids."""
    token = request.headers.get("X-Admin-Token", "")
    expected = os.environ.get("ADMIN_TOKEN", "")
    if expected and token != expected:
        return jsonify({"error": "unauthorized"}), 401

    # Returns frontend mock data to gracefully render "Planned Q2 2026" UI
    return jsonify({
        "status": "planned",
        "release": "Q2 2026",
        "description": "25-point geographic mesh resolution across target radius.",
        "stub_grid": [
            [2, 3, 3, 4, 5],
            [3, 4, 5, 6, 7],
            [4, 5, 7, 8, 9],
            [5, 6, 8, 9, 10],
            [6, 7, 9, 10, 11]
        ]
    })


# ── Testimonial Submission ────────────────────────────────────────────────────

TESTIMONIALS_FILE = Path(__file__).parent / "data" / "testimonials.json"

# Fallback owner email if ALERT_EMAIL env var is not set on the VPS
OWNER_EMAIL_FALLBACK = "sutrafloworg@gmail.com"


def _load_testimonials() -> dict:
    try:
        with open(TESTIMONIALS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"testimonials": []}


def _save_testimonials(data: dict) -> None:
    TESTIMONIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TESTIMONIALS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _send_testimonial_email(business_name, city, testimonial, submitter_email,
                             testimonial_id, approve_token, now_str):
    """Send approve/reject email to owner. Returns True on success."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    gmail_user     = config.GMAIL_USER
    gmail_password = config.GMAIL_APP_PASSWORD
    alert_email    = config.ALERT_EMAIL or OWNER_EMAIL_FALLBACK

    if not (gmail_user and gmail_password):
        logger.error("Testimonial: GMAIL_USER or GMAIL_APP_PASSWORD not set — cannot send email")
        return False

    base_url    = "https://api.sutraflow.org"
    approve_url = f"{base_url}/testimonial/approve/{testimonial_id}?token={approve_token}"
    reject_url  = f"{base_url}/testimonial/reject/{testimonial_id}?token={approve_token}"

    subject = f"⭐ New testimonial from {business_name or 'a visitor'} — needs your approval"
    html_body = f"""
<html><body style="font-family:sans-serif;max-width:600px;margin:auto;color:#1e293b;">
  <h2 style="color:#2563eb;">⭐ New Testimonial Submitted</h2>
  <table style="border-collapse:collapse;width:100%;margin-bottom:20px;">
    <tr><td style="padding:8px;border:1px solid #e2e8f0;font-weight:600;">Business</td>
        <td style="padding:8px;border:1px solid #e2e8f0;">{business_name or '(not provided)'}</td></tr>
    <tr><td style="padding:8px;border:1px solid #e2e8f0;font-weight:600;">City</td>
        <td style="padding:8px;border:1px solid #e2e8f0;">{city or '(not provided)'}</td></tr>
    <tr><td style="padding:8px;border:1px solid #e2e8f0;font-weight:600;">Submitter email</td>
        <td style="padding:8px;border:1px solid #e2e8f0;">{submitter_email or '(not provided)'}</td></tr>
    <tr><td style="padding:8px;border:1px solid #e2e8f0;font-weight:600;">Submitted at</td>
        <td style="padding:8px;border:1px solid #e2e8f0;">{now_str}</td></tr>
  </table>

  <div style="background:#f8fafc;border-left:4px solid #2563eb;padding:16px 20px;border-radius:0 8px 8px 0;font-style:italic;font-size:15px;line-height:1.6;margin-bottom:24px;">
    &ldquo;{testimonial}&rdquo;
  </div>

  <p style="margin-bottom:16px;font-size:14px;color:#64748b;">
    Click <strong>Approve</strong> to publish this on <strong>sutraflow.org/sentinel</strong>,
    or <strong>Reject</strong> to discard it. Both actions are permanent and instant.
  </p>

  <div style="display:flex;gap:12px;">
    <a href="{approve_url}"
       style="display:inline-block;padding:12px 28px;background:#16a34a;color:white;
              font-weight:700;font-size:15px;border-radius:8px;text-decoration:none;margin-right:12px;">
      ✅ Approve &amp; Publish
    </a>
    <a href="{reject_url}"
       style="display:inline-block;padding:12px 28px;background:#dc2626;color:white;
              font-weight:700;font-size:15px;border-radius:8px;text-decoration:none;">
      ❌ Reject
    </a>
  </div>
</body></html>
"""
    plain_body = (
        f"New testimonial from {business_name or 'a visitor'}.\n\n"
        f"\"{testimonial}\"\n\n"
        f"Business: {business_name or '(not provided)'}\n"
        f"City:     {city or '(not provided)'}\n"
        f"Email:    {submitter_email or '(not provided)'}\n"
        f"Time:     {now_str}\n\n"
        f"APPROVE: {approve_url}\n"
        f"REJECT:  {reject_url}\n"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"]  = subject
    msg["From"]     = f"Search Sentinel <{gmail_user}>"
    msg["To"]       = alert_email
    if submitter_email:
        msg["Reply-To"] = submitter_email
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_password)
        server.sendmail(gmail_user, [alert_email], msg.as_string())

    return True


@app.route("/testimonial", methods=["POST"])
def submit_testimonial():
    """Receive a testimonial from the website, store it as pending, email owner."""
    import secrets as _secrets

    data = request.get_json(silent=True) or {}
    business_name   = (data.get("business_name") or "").strip()[:100]
    city            = (data.get("city") or "").strip()[:80]
    testimonial     = (data.get("testimonial") or "").strip()[:2000]
    submitter_email = (data.get("email") or "").strip()[:120]

    if not testimonial or len(testimonial) < 15:
        return jsonify({"error": "testimonial_too_short"}), 400

    now_str        = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    testimonial_id = _secrets.token_hex(8)       # e.g. "a3f9c12b4d6e78a1"
    approve_token  = _secrets.token_hex(16)      # longer token for security

    entry = {
        "id":             testimonial_id,
        "approve_token":  approve_token,
        "status":         "pending",
        "business_name":  business_name,
        "city":           city,
        "testimonial":    testimonial,
        "email":          submitter_email,
        "submitted_at":   datetime.now(timezone.utc).isoformat(),
    }

    try:
        stored = _load_testimonials()
        stored["testimonials"].append(entry)
        _save_testimonials(stored)
    except Exception as e:
        logger.error(f"Testimonial: could not save to file: {e}")

    try:
        _send_testimonial_email(
            business_name, city, testimonial, submitter_email,
            testimonial_id, approve_token, now_str
        )
        logger.info(f"Testimonial: approval email sent for '{business_name}' (id={testimonial_id})")
    except Exception as e:
        logger.error(f"Testimonial: email failed: {e}")

    return jsonify({"success": True, "message": "Thank you for your testimonial!"})


def _testimonial_action_page(title: str, message: str, color: str) -> str:
    """Return a simple HTML confirmation page."""
    return f"""<!DOCTYPE html>
<html><head><title>{title}</title>
<style>body{{font-family:sans-serif;display:flex;align-items:center;justify-content:center;
min-height:100vh;margin:0;background:#f1f5f9;}}
.card{{background:white;border-radius:16px;padding:40px 48px;max-width:480px;text-align:center;
box-shadow:0 4px 24px rgba(0,0,0,.08);}}
h1{{color:{color};margin-bottom:12px;}} p{{color:#64748b;line-height:1.6;}}
a{{display:inline-block;margin-top:24px;padding:10px 24px;background:#2563eb;color:white;
border-radius:8px;text-decoration:none;font-weight:600;}}</style></head>
<body><div class="card">
  <h1>{title}</h1><p>{message}</p>
  <a href="https://sutraflow.org/sentinel">View Site</a>
</div></body></html>"""


@app.route("/testimonial/approve/<testimonial_id>", methods=["GET"])
def approve_testimonial(testimonial_id: str):
    """Owner clicks Approve in email → marks testimonial as approved and published."""
    token = request.args.get("token", "")
    try:
        stored = _load_testimonials()
        entry  = next((t for t in stored["testimonials"] if t.get("id") == testimonial_id), None)
        if not entry:
            return _testimonial_action_page("Not Found", "Testimonial not found.", "#dc2626"), 404
        if entry.get("approve_token") != token:
            return _testimonial_action_page("Unauthorized", "Invalid token.", "#dc2626"), 403
        if entry.get("status") == "approved":
            return _testimonial_action_page(
                "Already Published",
                f"This testimonial from {entry.get('business_name') or 'a visitor'} is already live.",
                "#2563eb"
            )
        entry["status"]      = "approved"
        entry["approved_at"] = datetime.now(timezone.utc).isoformat()
        _save_testimonials(stored)
        logger.info(f"Testimonial approved: id={testimonial_id}")
        return _testimonial_action_page(
            "✅ Testimonial Published!",
            f"The testimonial from <strong>{entry.get('business_name') or 'a visitor'}</strong> "
            f"is now live on sutraflow.org/sentinel.",
            "#16a34a"
        )
    except Exception as e:
        logger.error(f"Testimonial approve error: {e}")
        return _testimonial_action_page("Error", "Something went wrong. Try again.", "#dc2626"), 500


@app.route("/testimonial/reject/<testimonial_id>", methods=["GET"])
def reject_testimonial(testimonial_id: str):
    """Owner clicks Reject in email → marks testimonial as rejected (not published)."""
    token = request.args.get("token", "")
    try:
        stored = _load_testimonials()
        entry  = next((t for t in stored["testimonials"] if t.get("id") == testimonial_id), None)
        if not entry:
            return _testimonial_action_page("Not Found", "Testimonial not found.", "#dc2626"), 404
        if entry.get("approve_token") != token:
            return _testimonial_action_page("Unauthorized", "Invalid token.", "#dc2626"), 403
        entry["status"]     = "rejected"
        entry["rejected_at"] = datetime.now(timezone.utc).isoformat()
        _save_testimonials(stored)
        logger.info(f"Testimonial rejected: id={testimonial_id}")
        return _testimonial_action_page(
            "❌ Testimonial Rejected",
            "The testimonial has been discarded and will not appear on the site.",
            "#64748b"
        )
    except Exception as e:
        logger.error(f"Testimonial reject error: {e}")
        return _testimonial_action_page("Error", "Something went wrong. Try again.", "#dc2626"), 500


@app.route("/testimonials/public", methods=["GET"])
def public_testimonials():
    """Return approved testimonials for display on the website (no PII)."""
    stored = _load_testimonials()
    approved = [
        {
            "business_name": t.get("business_name") or "",
            "city":          t.get("city") or "",
            "testimonial":   t.get("testimonial") or "",
            "approved_at":   t.get("approved_at") or "",
        }
        for t in stored.get("testimonials", [])
        if t.get("status") == "approved"
    ]
    resp = jsonify({"testimonials": approved})
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


@app.route("/admin/testimonials", methods=["GET"])
def list_testimonials():
    """View all submitted testimonials (admin only)."""
    token    = request.headers.get("X-Admin-Token", "")
    expected = os.environ.get("ADMIN_TOKEN", "")
    if expected and token != expected:
        return jsonify({"error": "unauthorized"}), 401

    return jsonify(_load_testimonials())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    if not stripe.api_key:
        logger.warning("STRIPE_SECRET_KEY not set — webhook signature verification disabled")
    if not WEBHOOK_SECRET:
        logger.warning("STRIPE_WEBHOOK_SECRET not set — running without signature verification")

    logger.info(f"Starting webhook server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
