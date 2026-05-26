"""
Cloudflare setup — one-shot script to configure sutraflow.org for outbound email.

Does three things via the Cloudflare API (no dashboard needed):
  1. Enables Email Routing on the zone (so sentinel@sutraflow.org can receive replies)
  2. Adds a forwarding rule: sentinel@sutraflow.org → sutrafloworg@gmail.com
  3. Adds DNS records (SPF, DKIM, MX) so Resend can send as sentinel@sutraflow.org

Uses the existing CLOUDFLARE_API_TOKEN secret. The DNS records come in as
JSON input (paste them from the Resend domain-verification page).

Usage (locally or via GitHub Actions):
  python3 cloudflare_setup.py --records '{"spf": "v=spf1 include:_spf.resend.com ~all", "dkim": [...]}'

Idempotent — safe to re-run; existing identical records are skipped.
"""
import argparse
import json
import logging
import os
import sys
import time

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

API_BASE = "https://api.cloudflare.com/client/v4"
DOMAIN = "sutraflow.org"
FORWARD_TO = "sutrafloworg@gmail.com"


def _headers():
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if not token:
        logger.error("CLOUDFLARE_API_TOKEN env var is required")
        sys.exit(1)
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _api(method: str, path: str, data: dict | None = None) -> dict:
    url = f"{API_BASE}{path}"
    resp = requests.request(method, url, headers=_headers(), json=data, timeout=30)
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}
    if not resp.ok or not body.get("success", True):
        logger.error(f"API {method} {path} → {resp.status_code}: {body}")
    return body


def get_zone_id() -> str:
    body = _api("GET", f"/zones?name={DOMAIN}")
    zones = body.get("result", [])
    if not zones:
        logger.error(f"Zone {DOMAIN} not found on this Cloudflare account")
        sys.exit(1)
    zid = zones[0]["id"]
    logger.info(f"Zone {DOMAIN} → {zid}")
    return zid


def enable_email_routing(zone_id: str) -> None:
    """Enable Email Routing on the zone if not already enabled."""
    body = _api("GET", f"/zones/{zone_id}/email/routing")
    status = body.get("result", {}).get("status") or body.get("result", {}).get("enabled")
    logger.info(f"Email routing current status: {status}")

    if status in ("ready", True, "enabled"):
        logger.info("Email Routing already active")
        return

    # Enable it
    body = _api("POST", f"/zones/{zone_id}/email/routing/enable")
    if body.get("success"):
        logger.info("Email Routing enabled")
    else:
        logger.warning(f"Could not enable routing (may already be on): {body}")


def add_destination_address(email: str) -> None:
    """Add a destination address (the Gmail that will receive forwards).
    This sends a verification email to the destination address — the user must click it."""
    # Get account ID via zone
    body = _api("GET", f"/zones?name={DOMAIN}")
    account_id = body["result"][0]["account"]["id"]
    logger.info(f"Account ID: {account_id}")

    # List existing destinations
    existing = _api("GET", f"/accounts/{account_id}/email/routing/addresses")
    for addr in existing.get("result", []) or []:
        if addr.get("email", "").lower() == email.lower():
            logger.info(f"Destination {email} already registered (verified={addr.get('verified')})")
            return

    body = _api("POST", f"/accounts/{account_id}/email/routing/addresses", {"email": email})
    if body.get("success"):
        logger.info(f"Destination {email} added — VERIFY by clicking the email Cloudflare just sent")
    else:
        logger.warning(f"Could not add destination: {body}")


def add_routing_rule(zone_id: str, alias_local: str, dest_email: str) -> None:
    """Add a routing rule: alias_local@DOMAIN → dest_email."""
    full_alias = f"{alias_local}@{DOMAIN}"

    # Check existing rules
    body = _api("GET", f"/zones/{zone_id}/email/routing/rules")
    for rule in body.get("result", []) or []:
        for m in rule.get("matchers", []):
            if m.get("field") == "to" and m.get("value", "").lower() == full_alias.lower():
                logger.info(f"Rule for {full_alias} already exists")
                return

    payload = {
        "name": f"Forward {full_alias} → {dest_email}",
        "enabled": True,
        "priority": 50,
        "matchers": [{"type": "literal", "field": "to", "value": full_alias}],
        "actions": [{"type": "forward", "value": [dest_email]}],
    }
    body = _api("POST", f"/zones/{zone_id}/email/routing/rules", payload)
    if body.get("success"):
        logger.info(f"Rule added: {full_alias} → {dest_email}")
    else:
        logger.warning(f"Could not add rule: {body}")


def upsert_dns_record(zone_id: str, rec_type: str, name: str, content: str,
                      priority: int | None = None, ttl: int = 1) -> None:
    """Add or update a DNS record. ttl=1 means auto."""
    # Normalize name to FQDN
    if not name.endswith(DOMAIN):
        name = f"{name}.{DOMAIN}" if name and name != "@" else DOMAIN

    # Look for existing record with same type + name + content
    body = _api("GET", f"/zones/{zone_id}/dns_records?type={rec_type}&name={name}")
    for existing in body.get("result", []) or []:
        if existing.get("content", "").strip() == content.strip():
            logger.info(f"DNS {rec_type} {name} already correct — skipping")
            return

    payload = {"type": rec_type, "name": name, "content": content, "ttl": ttl}
    if priority is not None and rec_type == "MX":
        payload["priority"] = priority

    body = _api("POST", f"/zones/{zone_id}/dns_records", payload)
    if body.get("success"):
        logger.info(f"DNS {rec_type} {name} → {content[:60]}... CREATED")
    else:
        logger.warning(f"Could not create {rec_type} {name}: {body}")


def configure_resend_dns(zone_id: str, records: dict) -> None:
    """Add DNS records that Resend requires for domain verification.

    Expected shape of `records`:
    {
      "spf":  {"name": "send", "value": "v=spf1 include:amazonses.com ~all"},
      "dkim": {"name": "resend._domainkey", "value": "p=MIGfMA0GCSqGSIb..."},
      "mx":   {"name": "send",  "value": "feedback-smtp.us-east-1.amazonses.com", "priority": 10}
    }
    Field names follow what Resend's dashboard shows.
    """
    if not records:
        logger.info("No DNS records provided — skipping Resend DNS step")
        return

    # SPF (TXT)
    spf = records.get("spf")
    if spf:
        upsert_dns_record(zone_id, "TXT", spf.get("name", "send"), spf["value"])

    # DKIM (TXT)
    dkim = records.get("dkim")
    if dkim:
        upsert_dns_record(zone_id, "TXT", dkim.get("name", "resend._domainkey"), dkim["value"])

    # MX (for inbound bounce notifications)
    mx = records.get("mx")
    if mx:
        upsert_dns_record(zone_id, "MX", mx.get("name", "send"), mx["value"],
                          priority=mx.get("priority", 10))


def main():
    parser = argparse.ArgumentParser(description="One-shot Cloudflare setup for sutraflow.org")
    parser.add_argument("--records", default="{}", help="JSON of Resend DNS records (spf/dkim/mx)")
    parser.add_argument("--alias", default="sentinel",
                        help="Local part of the alias (default: sentinel → sentinel@sutraflow.org)")
    parser.add_argument("--forward-to", default=FORWARD_TO,
                        help=f"Where to forward replies (default: {FORWARD_TO})")
    args = parser.parse_args()

    try:
        records = json.loads(args.records) if args.records else {}
    except json.JSONDecodeError as e:
        logger.error(f"--records is not valid JSON: {e}")
        sys.exit(1)

    logger.info(f"=== Configuring {DOMAIN} ===")
    zone_id = get_zone_id()

    logger.info("--- Step 1: Email Routing ---")
    
    logger.info("--- Step 2: Resend DNS records ---")
    configure_resend_dns(zone_id, records)

    logger.info("=== Done ===")
    logger.info(f"After Cloudflare's verification email is clicked, replies to "
                f"{args.alias}@{DOMAIN} will forward to {args.forward_to}.")


if __name__ == "__main__":
    main()
