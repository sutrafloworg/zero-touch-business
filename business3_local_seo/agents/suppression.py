"""
Suppression / Do-Not-Contact list.

Why this exists:
    Cold-email senders that keep mailing the same address forever (especially after
    follow-up #3 with no response, or after a hard bounce, or after the recipient
    replies "unsubscribe") lose their sender reputation fast. Gmail flags them,
    deliverability collapses, and the whole acquisition channel dies.

    This module gives the rest of the codebase a single source of truth for
    "do not email this address — ever."

Sources of suppression (all funnel into the same JSON file):
  1. User-replied "unsubscribe" — added manually via `add` CLI
  2. 3 follow-ups + no payment (auto-added by followup_sequence after followup_3 fires)
  3. Hard bounce — future, when bounce parsing is wired up
  4. Placeholder emails that slipped through is_valid_outreach_email — auto-added on detection

File format (data/suppression.json):
{
  "emails": {
    "owner@example.com": {"added_at": "...", "reason": "followup_3_completed"},
    ...
  }
}

Lookups are O(1) on email; the file is small and rewritten atomically.
"""
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Support both `from agents.suppression import ...` (as a module) AND
# `python3 agents/suppression.py ...` (as a CLI from the project root).
if __package__ in (None, "") and "agents" not in sys.modules:
    _BUSINESS_ROOT = Path(__file__).resolve().parent.parent
    if str(_BUSINESS_ROOT) not in sys.path:
        sys.path.insert(0, str(_BUSINESS_ROOT))

from agents.json_store import atomic_write_json, safe_load_json

logger = logging.getLogger(__name__)

SUPPRESSION_FILE = Path(__file__).parent.parent / "data" / "suppression.json"


def _normalize(email: str) -> str:
    return (email or "").lower().strip()


def _load() -> dict:
    data = safe_load_json(SUPPRESSION_FILE, {"emails": {}})
    if "emails" not in data:
        data["emails"] = {}
    return data


def _save(data: dict) -> None:
    atomic_write_json(SUPPRESSION_FILE, data)


def is_suppressed(email: str) -> bool:
    """Return True if this email should NOT be contacted."""
    norm = _normalize(email)
    if not norm:
        return True  # empty/None emails are always suppressed
    return norm in _load()["emails"]


def suppress(email: str, reason: str = "manual") -> bool:
    """Add an email to the suppression list. Idempotent. Returns True if newly added."""
    norm = _normalize(email)
    if not norm:
        return False
    data = _load()
    if norm in data["emails"]:
        return False
    data["emails"][norm] = {
        "added_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
    }
    _save(data)
    logger.info(f"Suppression: added {norm} (reason: {reason})")
    return True


def unsuppress(email: str) -> bool:
    """Remove an email from the suppression list. Returns True if it was present."""
    norm = _normalize(email)
    data = _load()
    if norm not in data["emails"]:
        return False
    del data["emails"][norm]
    _save(data)
    logger.info(f"Suppression: removed {norm}")
    return True


def list_suppressed() -> dict:
    """Return the full suppression dict (for admin views / debugging)."""
    return _load()


def filter_safe(emails) -> list:
    """Given an iterable of emails, return only those NOT suppressed."""
    sup = _load()["emails"]
    return [e for e in emails if _normalize(e) and _normalize(e) not in sup]


# ── CLI for manual ops (mark unsubscribe replies, audit list, etc.) ────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Suppression list manager")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Suppress an email")
    p_add.add_argument("email")
    p_add.add_argument("--reason", default="manual")

    p_rm = sub.add_parser("remove", help="Unsuppress an email")
    p_rm.add_argument("email")

    p_chk = sub.add_parser("check", help="Check if an email is suppressed")
    p_chk.add_argument("email")

    sub.add_parser("list", help="List all suppressed emails")

    args = parser.parse_args()

    if args.cmd == "add":
        added = suppress(args.email, args.reason)
        print(f"{'added' if added else 'already suppressed'}: {args.email}")
    elif args.cmd == "remove":
        removed = unsuppress(args.email)
        print(f"{'removed' if removed else 'not present'}: {args.email}")
    elif args.cmd == "check":
        print(f"{args.email}: {'SUPPRESSED' if is_suppressed(args.email) else 'ok to contact'}")
    elif args.cmd == "list":
        data = list_suppressed()
        for email, meta in sorted(data["emails"].items()):
            print(f"  {email}\t{meta.get('added_at','')}\t{meta.get('reason','')}")
        print(f"\nTotal: {len(data['emails'])}")
    sys.exit(0)
