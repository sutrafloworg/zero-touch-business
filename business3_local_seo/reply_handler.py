"""
Reply handler — reads the Gmail inbox via IMAP and does two revenue-critical jobs:

1. UNSUBSCRIBE AUTOMATION: our emails say "reply 'unsubscribe'". This script
   actually honors that — any reply with unsubscribe intent gets the sender
   added to the suppression list so we never email them again.

2. WARM-LEAD ALERTING: any genuine reply from a prospect (someone in our
   pending_reports lead list) is a warm lead. The owner gets a digest email
   listing who replied, so they can respond personally within hours.

Uses the same GMAIL_APP_PASSWORD as SMTP — no new credentials.
Runs daily via .github/workflows/bounce_handler.yml (after the bounce scan).
"""

from __future__ import annotations

import argparse
import email
import email.utils
import imaplib
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config  # noqa: E402
from agents import suppression  # noqa: E402
from agents.json_store import atomic_write_json, safe_load_json  # noqa: E402
from agents.email_sender import send_email  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("reply_handler")

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

DATA_DIR = Path(__file__).parent / "data"
SEEN_FILE = DATA_DIR / "replies_seen.json"

UNSUB_RE = re.compile(
    r"unsubscribe|remove\s+me|stop\s+email|opt\s*[- ]?out|take\s+me\s+off"
    r"|do\s+not\s+(?:contact|email)|no\s+longer\s+wish|quit\s+emailing",
    re.IGNORECASE,
)

AUTOMATED_SENDERS = (
    "mailer-daemon", "postmaster", "no-reply", "noreply", "do-not-reply",
    "notifications@", "notification@", "bounce", "auto-confirm",
)


def _decode(value) -> str:
    if not value:
        return ""
    try:
        parts = email.header.decode_header(value)
        out = []
        for text, enc in parts:
            if isinstance(text, bytes):
                out.append(text.decode(enc or "utf-8", errors="replace"))
            else:
                out.append(text)
        return "".join(out)
    except Exception:
        return str(value)


def _get_body(msg) -> str:
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        html = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                        return re.sub(r"<[^>]+>", " ", html)
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        pass
    return ""


def _load_lead_emails() -> dict:
    """email (lowercase) -> business_name, from pending_reports.json."""
    data = safe_load_json(DATA_DIR / "pending_reports.json", {"reports": []})
    out = {}
    for r in data.get("reports", []):
        addr = (r.get("email") or "").strip().lower()
        if addr:
            out[addr] = r.get("business_name", addr)
    return out


def _load_seen() -> set:
    return set(safe_load_json(SEEN_FILE, []))


def _save_seen(seen: set) -> None:
    # keep the newest 2000 ids to bound file size
    atomic_write_json(SEEN_FILE, sorted(seen)[-2000:])


def _is_automated(from_addr: str) -> bool:
    f = from_addr.lower()
    return any(s in f for s in AUTOMATED_SENDERS)


def scan_replies(days: int = 14, dry_run: bool = False) -> dict:
    user, pw = config.GMAIL_USER, config.GMAIL_APP_PASSWORD
    if not user or not pw:
        logger.error("reply_handler: GMAIL_USER or GMAIL_APP_PASSWORD missing")
        return {"error": "no_credentials"}

    stats = {"scanned": 0, "unsubscribed": 0, "warm_replies": 0, "skipped_seen": 0}
    leads = _load_lead_emails()
    seen = _load_seen()
    warm: list[dict] = []
    unsubbed: list[str] = []

    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(user, pw)
        imap.select("INBOX")
    except Exception as e:
        logger.error(f"reply_handler: IMAP login failed: {e}")
        return {"error": f"imap_login_failed: {e}"}

    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
        typ, data = imap.search(None, f'(SINCE "{since}")')
        if typ != "OK":
            return stats
        msg_ids = (data[0] or b"").split()
        logger.info(f"reply_handler: {len(msg_ids)} messages since {since}")

        for msg_id in msg_ids:
            typ, msg_data = imap.fetch(msg_id, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])

            mid = _decode(msg.get("Message-ID", "")).strip() or f"no-id-{msg_id.decode()}"
            if mid in seen:
                stats["skipped_seen"] += 1
                continue

            from_hdr = _decode(msg.get("From", ""))
            from_addr = (email.utils.parseaddr(from_hdr)[1] or "").lower()
            subject = _decode(msg.get("Subject", ""))
            stats["scanned"] += 1
            seen.add(mid)

            if not from_addr or from_addr == user.lower() or _is_automated(from_addr):
                continue

            body = _get_body(msg)
            text = f"{subject}\n{body[:4000]}"

            if UNSUB_RE.search(text):
                stats["unsubscribed"] += 1
                unsubbed.append(from_addr)
                if dry_run:
                    logger.info(f"  [DRY RUN] would suppress (unsubscribe reply): {from_addr}")
                else:
                    suppression.suppress(from_addr, reason="unsubscribe_reply")
                    logger.info(f"  suppressed (unsubscribe reply): {from_addr}")
                continue

            if from_addr in leads:
                stats["warm_replies"] += 1
                snippet = re.sub(r"\s+", " ", body).strip()[:300]
                warm.append({
                    "email": from_addr,
                    "business": leads[from_addr],
                    "subject": subject[:120],
                    "snippet": snippet,
                })
                logger.info(f"  WARM REPLY from {leads[from_addr]} <{from_addr}>: {subject[:80]!r}")

        imap.close()
        imap.logout()
    except Exception as e:
        logger.error(f"reply_handler: scan error: {e}", exc_info=True)
        try:
            imap.logout()
        except Exception:
            pass
        return {"error": f"scan_error: {e}", **stats}

    if not dry_run:
        _save_seen(seen)

    # Alert the owner about warm replies — these are the hottest leads we have.
    if warm and not dry_run:
        rows = "".join(
            f"<tr><td style='padding:6px 10px;border-bottom:1px solid #eee'><b>{w['business']}</b><br>"
            f"<a href='mailto:{w['email']}'>{w['email']}</a></td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{w['subject']}<br>"
            f"<span style='color:#666;font-size:12px'>{w['snippet']}</span></td></tr>"
            for w in warm
        )
        html = (
            f"<div style='font-family:sans-serif;max-width:640px'>"
            f"<h2>🔥 {len(warm)} prospect repl{'y' if len(warm)==1 else 'ies'} waiting</h2>"
            f"<p>These businesses replied to Search Sentinel outreach. Reply personally "
            f"within 24h — warm replies are the highest-converting leads you have.</p>"
            f"<table style='border-collapse:collapse'>{rows}</table>"
            f"<p style='color:#666;font-size:12px'>Sent automatically by reply_handler.py</p></div>"
        )
        plain = "\n\n".join(f"{w['business']} <{w['email']}>: {w['subject']}\n{w['snippet']}" for w in warm)
        send_email(
            to_email=user,
            subject=f"[Search Sentinel] {len(warm)} warm repl{'y' if len(warm)==1 else 'ies'} — respond today",
            html=html,
            plain=plain,
            from_name="Search Sentinel Bot",
        )
        logger.info(f"reply_handler: owner alert sent for {len(warm)} warm replies")

    logger.info(f"reply_handler: done — {stats}")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan inbox for unsubscribe replies + warm leads")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()
    result = scan_replies(days=args.days, dry_run=args.dry_run)
    print(f"\nResult: {result}")
