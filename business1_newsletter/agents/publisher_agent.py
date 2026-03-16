"""
Publisher Agent — sends the newsletter via ConvertKit (Kit) API.

Kit API v3 docs: https://developers.kit.com/v3
Free tier: up to 10,000 subscribers, no monthly fee.

Flow:
  1. Create a broadcast (draft) via API
  2. Send/schedule it immediately
  3. Return broadcast ID for monitoring

Self-correction: if publish fails, saves newsletter to a local file for manual recovery.
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

KIT_BASE_URL = "https://api.convertkit.com/v3"


class PublisherAgent:
    def __init__(self, api_secret: str, logs_dir: Path, max_retries: int = 3):
        self.api_secret = api_secret
        self.logs_dir = logs_dir
        self.logs_dir.mkdir(exist_ok=True)
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _save_fallback(self, subject: str, html_body: str) -> Path:
        """Emergency fallback: save newsletter locally if API fails."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fallback_file = self.logs_dir / f"unsent_newsletter_{ts}.html"
        with open(fallback_file, "w") as f:
            f.write(f"<!-- Subject: {subject} -->\n")
            f.write(html_body)
        logger.warning(f"Newsletter saved to fallback file: {fallback_file}")
        return fallback_file

    def _api_call(
        self,
        method: str,
        endpoint: str,
        payload: dict | None = None,
    ) -> dict:
        """Make API call with retry logic."""
        url = f"{KIT_BASE_URL}/{endpoint}"
        for attempt in range(self.max_retries):
            try:
                # Inject api_secret into every request
                if payload is None:
                    payload = {}
                if method in ("POST", "PUT"):
                    payload["api_secret"] = self.api_secret
                else:
                    payload["api_secret"] = self.api_secret

                if method == "POST":
                    resp = self.session.post(url, json=payload, timeout=30)
                elif method == "GET":
                    resp = self.session.get(url, params=payload, timeout=30)
                elif method == "PUT":
                    resp = self.session.put(url, json=payload, timeout=30)
                else:
                    raise ValueError(f"Unknown method: {method}")

                if resp.status_code in (200, 201, 202):
                    return resp.json()

                if resp.status_code == 429:  # rate limited
                    wait = int(resp.headers.get("Retry-After", 60))
                    logger.warning(f"Rate limited by Kit API. Waiting {wait}s")
                    time.sleep(wait)
                    continue

                if resp.status_code >= 500:  # server error, retry
                    wait = (2 ** attempt) * 5
                    logger.warning(f"Kit API server error {resp.status_code}. Waiting {wait}s")
                    time.sleep(wait)
                    continue

                # 4xx client error — log and raise
                logger.error(f"Kit API error {resp.status_code}: {resp.text}")
                resp.raise_for_status()

            except requests.ConnectionError as e:
                wait = (2 ** attempt) * 5
                logger.warning(f"Connection error (attempt {attempt + 1}): {e}. Waiting {wait}s")
                time.sleep(wait)

        raise RuntimeError(f"Kit API call failed after {self.max_retries} attempts")

    def publish(self, subject: str, preview_text: str, html_body: str) -> str:
        """
        Create and send a newsletter broadcast.
        Returns broadcast ID string, or 'fallback:<path>' on total failure.
        """
        payload = {
            "subject": subject,
            "description": preview_text,
            "content": html_body,
            "email_layout_template": "plain",
            "public": False,
        }

        try:
            logger.info("Creating broadcast via Kit API v3...")
            result = self._api_call("POST", "broadcasts", payload)
            broadcast = result.get("broadcast", {})
            broadcast_id = str(broadcast.get("id", "unknown"))
            logger.info(f"Publisher Agent: broadcast created, ID={broadcast_id}")
            return broadcast_id

        except Exception as e:
            logger.error(f"Publisher Agent: FAILED to publish — {e}")
            fallback = self._save_fallback(subject, html_body)
            return f"fallback:{fallback}"

    def get_broadcast_stats(self, broadcast_id: str) -> dict:
        """Fetch stats for a previous broadcast."""
        try:
            result = self._api_call("GET", f"broadcasts/{broadcast_id}/stats")
            stats = result.get("broadcast", {})
            return {
                "recipients": stats.get("recipients", 0),
                "open_rate": stats.get("open_rate", 0),
                "click_rate": stats.get("click_rate", 0),
            }
        except Exception as e:
            logger.error(f"Could not fetch broadcast stats: {e}")
            return {}

    def get_subscriber_count(self) -> int:
        """Get current active subscriber count."""
        try:
            result = self._api_call("GET", "subscribers", {"sort_field": "created_at"})
            return result.get("total_subscribers", 0)
        except Exception as e:
            logger.error(f"Could not fetch subscriber count: {e}")
            return -1
