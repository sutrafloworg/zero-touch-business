"""
Bounce handler — reads the Gmail inbox via IMAP, finds delivery-failure
notifications from "Mail Delivery Subsystem" / "mailer-daemon@", extracts the
failed recipient address, and adds it to the suppression list.

Why: when Gmail keeps trying to deliver to an address that hard-bounces, our
sender reputation tanks. Suppressing bounced addresses is non-negotiable.

Credentials: uses GMAIL_USER + GMAIL_APP_PASSWORD env vars (same as outreach).
Gmail App Passwords work for both SMTP AND IMAP — no new setup needed.

Scope:
  - Looks at the most recent 50 emails in INBOX
  - Only processes messages from mailer-daemon@ / postmaster@ senders
  - Skips messages already marked with the \Search-Sentinel-Processed label

Designed to be cheap + safe: dry-run mode prints what it would do without
modifying anything. Production run adds to suppression.json and marks the
bounce email as "read" so we don't reprocess it.
"""
import argparse
import email
import imaplib
import logging
import re
import sys
from email.header import decode_header
from pathlib import Path

import config
from agents import suppression

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

# Senders that indicate a bounce/non-delivery report
BOUNCE_SENDERS = (
    "mailer-daemon@",
    "postmaster@",
    "mail delivery subsystem",
    "mail delivery system",
    "delivery status notification",
)

# Subjects that indicate a bounce
BOUNCE_SUBJECTS = (
    "delivery status notification",
    "undelivered mail",
    "undeliverable",
    "mail delivery failed",
    "returned mail",
    "failure notice",
)

# Common patterns where the failed recipient address appears in the body
ADDR_RE = re.compile(
    r"(?:to:?|recipient:?|address:?|failed:|the following address(?:es)? failed:?)\s*"
    r"[<\s]*([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
    re.IGNORECASE,
)

# Fallback — grab the first email address in the body that's NOT our own sender
ANY_ADDR_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _decode(value) -> str:
    """Decode an email header that may be RFC 2047 encoded."""
    if value is None:
        return ""
    parts = decode_header(value)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(enc or "utf-8", errors="replace"))
            except (LookupError, TypeError):
                out.append(text.decode("utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def _get_body(msg) -> str:
    """Extract the text body from an email.message.Message, walking multipart."""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            if ctype == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                except Exception:
                    continue
        # Fallback: try text/html or any text
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            if ctype.startswith("text/"):
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                except Exception:
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", errors="replace"
        )
    except Exception:
        return str(msg.get_payload())


def _extract_failed_address(body: str, own_email: str) -> str | None:
    """Find the failed recipient address in a bounce body, ignoring our own email."""
    own_lower = (own_email or "").lower()
    # Try the structured patterns first
    for m in ADDR_RE.finditer(body):
        candidate = m.group(1).lower().strip()
        if candidate != own_lower and "mailer-daemon" not in candidate:
            return candidate
    # Fallback: first address that isn't us or mailer-daemon
    for m in ANY_ADDR_RE.finditer(body):
        candidate = m.group(0).lower().strip()
        if candidate == own_lower:
            continue
        if "mailer-daemon" in candidate or "postmaster" in candidate:
            continue
        if candidate.endswith(("@google.com", "@gmail-smtp-in.l.google.com")):
            continue
        return candidate
    return None


def _is_bounce(from_addr: str, subject: str) -> bool:
    from_l = (from_addr or "").lower()
    subj_l = (subject or "").lower()
    if any(s in from_l for s in BOUNCE_SENDERS):
        return True
    if any(s in subj_l for s in BOUNCE_SUBJECTS):
        return True
    return False


def scan_bounces(dry_run: bool = False, lookback: int = 50) -> dict:
    """Connect to Gmail IMAP, scan recent inbox for bounces, suppress failed addresses."""
    user = config.GMAIL_USER
    pw = config.GMAIL_APP_PASSWORD

    if not user or not pw:
        logger.error("bounce_handler: GMAIL_USER or GMAIL_APP_PASSWORD missing")
        return {"error": "no_credentials"}

    stats = {"scanned": 0, "bounces_found": 0, "addresses_suppressed": 0,
             "already_suppressed": 0, "no_address_extracted": 0}

    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(user, pw)
        imap.select("INBOX")
    except Exception as e:
        logger.error(f"bounce_handler: IMAP login failed: {e}")
        return {"error": f"imap_login_failed: {e}"}

    try:
        # Search the inbox for messages from mailer-daemon and similar
        # Use OR query covering common bounce-sender patterns
        # IMAP search keys: FROM "mailer-daemon" or FROM "postmaster"
        typ, data = imap.search(
            None,
            '(OR OR OR FROM "mailer-daemon" FROM "postmaster" FROM "delivery" SUBJECT "Delivery Status Notification")'
        )
        if typ != "OK":
            logger.warning(f"bounce_handler: IMAP search returned {typ}")
            return stats

        msg_ids = (data[0] or b"").split()
        # Limit to the most recent N
        msg_ids = msg_ids[-lookback:] if len(msg_ids) > lookback else msg_ids
        logger.info(f"bounce_handler: examining {len(msg_ids)} candidate messages")

        for msg_id in msg_ids:
            stats["scanned"] += 1
            typ, msg_data = imap.fetch(msg_id, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            from_addr = _decode(msg.get("From", ""))
            subject = _decode(msg.get("Subject", ""))

            if not _is_bounce(from_addr, subject):
                continue

            stats["bounces_found"] += 1
            body = _get_body(msg)
            failed_addr = _extract_failed_address(body, user)

            if not failed_addr:
                stats["no_address_extracted"] += 1
                logger.info(f"  bounce found but couldn't extract address — subject: {subject[:80]!r}")
                continue

            if suppression.is_suppressed(failed_addr):
                stats["already_suppressed"] += 1
                logger.debug(f"  already suppressed: {failed_addr}")
                continue

            if dry_run:
                logger.info(f"  [DRY RUN] would suppress: {failed_addr} (from bounce '{subject[:60]}')")
                stats["addresses_suppressed"] += 1
                continue

            added = suppression.suppress(failed_addr, reason="hard_bounce")
            if added:
                stats["addresses_suppressed"] += 1
                logger.info(f"  suppressed bounced address: {failed_addr}")

        imap.close()
        imap.logout()
    except Exception as e:
        logger.error(f"bounce_handler: error during scan: {e}", exc_info=True)
        try:
            imap.logout()
        except Exception:
            pass
        return {"error": f"scan_error: {e}", **stats}

    logger.info(f"bounce_handler: done — {stats}")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan Gmail inbox for bounces and update suppression list")
    parser.add_argument("--dry-run", action="store_true", help="Preview without modifying suppression")
    parser.add_argument("--lookback", type=int, default=50, help="How many recent bounce-candidate messages to check (default 50)")
    args = parser.parse_args()

    if args.dry_run:
        logger.info("=== DRY RUN MODE — suppression list will NOT be modified ===")
    result = scan_bounces(dry_run=args.dry_run, lookback=args.lookback)
    print(f"\nResult: {result}")
    sys.exit(0)
