"""
Unified email sender.

Two backends:
  1. Resend API (preferred when RESEND_API_KEY is set) — sends from sentinel@sutraflow.org,
     dramatically better deliverability than Gmail SMTP for bulk outreach.
  2. Gmail SMTP — fallback when Resend not configured.

The rest of the codebase calls send_email(...) without caring which one fires.
"""
import logging
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
DEFAULT_FROM_DOMAIN = "sutraflow.org"
DEFAULT_FROM_LOCAL = "sentinel"


def _have_resend() -> bool:
    return bool(os.environ.get("RESEND_API_KEY"))


def _from_address(from_name: str) -> str:
    """Pick the From: address. Resend uses sentinel@sutraflow.org;
    Gmail SMTP uses the GMAIL_USER value (must match login)."""
    if _have_resend():
        local = os.environ.get("RESEND_FROM_LOCAL", DEFAULT_FROM_LOCAL)
        domain = os.environ.get("RESEND_FROM_DOMAIN", DEFAULT_FROM_DOMAIN)
        return f"{from_name} <{local}@{domain}>"
    gmail_user = os.environ.get("GMAIL_USER", "")
    return f"{from_name} <{gmail_user}>"


def _reply_to_address() -> str:
    """Always set Reply-To to the real human inbox so replies reach you."""
    return os.environ.get("ALERT_EMAIL") or os.environ.get("GMAIL_USER") or ""


def _send_via_resend(
    *,
    to_email: str,
    subject: str,
    html: str | None,
    plain: str | None,
    from_name: str,
    headers: dict | None,
    pdf_path: Path | None,
) -> bool:
    """Send via Resend API. Returns True on success."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    payload = {
        "from": _from_address(from_name),
        "to": [to_email],
        "subject": subject,
        "reply_to": _reply_to_address(),
    }
    if html:
        payload["html"] = html
    if plain:
        payload["text"] = plain
    if headers:
        # Resend accepts a `headers` dict for custom headers (List-Unsubscribe etc.)
        payload["headers"] = headers
    if pdf_path and Path(pdf_path).exists():
        import base64
        with open(pdf_path, "rb") as f:
            payload["attachments"] = [{
                "filename": Path(pdf_path).name,
                "content": base64.b64encode(f.read()).decode("ascii"),
            }]

    try:
        resp = requests.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        if resp.status_code in (200, 202):
            logger.info(f"Resend: sent to {to_email} (id={resp.json().get('id','?')})")
            return True
        logger.error(f"Resend: {resp.status_code} → {resp.text[:300]}")
        return False
    except Exception as e:
        logger.error(f"Resend: exception sending to {to_email}: {e}")
        return False


def _send_via_gmail(
    *,
    to_email: str,
    subject: str,
    html: str | None,
    plain: str | None,
    from_name: str,
    headers: dict | None,
    pdf_path: Path | None,
) -> bool:
    """Send via Gmail SMTP. Returns True on success."""
    user = os.environ.get("GMAIL_USER", "")
    pw = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not user or not pw:
        logger.error("Gmail SMTP: credentials missing")
        return False

    if pdf_path and Path(pdf_path).exists():
        msg = MIMEMultipart("mixed")
        body = MIMEMultipart("alternative")
        if plain:
            body.attach(MIMEText(plain, "plain"))
        if html:
            body.attach(MIMEText(html, "html"))
        msg.attach(body)
        with open(pdf_path, "rb") as f:
            pdf = MIMEApplication(f.read(), _subtype="pdf")
            pdf.add_header("Content-Disposition", "attachment",
                           filename=Path(pdf_path).name)
            msg.attach(pdf)
    else:
        msg = MIMEMultipart("alternative")
        if plain:
            msg.attach(MIMEText(plain, "plain"))
        if html:
            msg.attach(MIMEText(html, "html"))

    msg["Subject"] = subject
    msg["From"] = _from_address(from_name)
    msg["To"] = to_email
    msg["Reply-To"] = _reply_to_address() or user
    if headers:
        for k, v in headers.items():
            msg[k] = v

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(user, pw)
            server.sendmail(user, [to_email], msg.as_string())
        logger.info(f"Gmail SMTP: sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Gmail SMTP: failed sending to {to_email}: {e}")
        return False


def send_email(
    *,
    to_email: str,
    subject: str,
    html: str | None = None,
    plain: str | None = None,
    from_name: str = "Search Sentinel",
    headers: dict | None = None,
    pdf_path: Path | None = None,
) -> bool:
    """Send an email via Resend (if configured) or Gmail SMTP.

    Returns True on success. Both backends respect List-Unsubscribe headers if
    you pass them in via `headers`.
    """
    if _have_resend():
        if _send_via_resend(
            to_email=to_email, subject=subject, html=html, plain=plain,
            from_name=from_name, headers=headers, pdf_path=pdf_path,
        ):
            return True
        # AUTONOMY: Resend can fail mid-run — most commonly the free-tier
        # 100/email/day quota (HTTP 429), but also transient 5xx. Rather than
        # silently dropping the message (which previously stalled report
        # delivery and outreach), fall back to Gmail SMTP for this send.
        logger.warning(
            f"Resend send to {to_email} failed (quota or transient error) -- "
            "falling back to Gmail SMTP."
        )
    return _send_via_gmail(
        to_email=to_email, subject=subject, html=html, plain=plain,
        from_name=from_name, headers=headers, pdf_path=pdf_path,
    )


def active_backend() -> str:
    """Return 'resend' or 'gmail' — handy for logging."""
    return "resend" if _have_resend() else "gmail"
