"""
Publisher Agent — saves articles to the Hugo content directory.

In GitHub Actions, the workflow:
  1. Checks out the repo
  2. Runs this script (which writes .md files to hugo_site/content/posts/)
  3. Commits and pushes the new files
  4. Cloudflare Pages detects the push and auto-builds+deploys the Hugo site

This agent handles:
  - Writing markdown files
  - Updating the sitemap ping (notifies Google of new pages)
  - Logging what was published
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


class PublisherAgent:
    def __init__(self, content_dir: Path, state_file: Path, site_domain: str):
        self.content_dir = content_dir
        self.content_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = state_file
        self.site_domain = site_domain
        self.published_this_run: list[str] = []

    def _load_state(self) -> dict:
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_state(self, updates: dict) -> None:
        state = self._load_state()
        state.update(updates)
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2, default=str)

    def publish_article(self, slug: str, content: str) -> bool:
        """Write article markdown file to Hugo content directory."""
        try:
            output_path = self.content_dir / f"{slug}.md"
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content)
            self.published_this_run.append(slug)
            logger.info(f"Publisher: saved {output_path.name}")
            return True
        except Exception as e:
            logger.error(f"Publisher: failed to save {slug}: {e}")
            return False

    def ping_google_indexing(self, slugs: list[str]) -> None:
        """
        Ping Google Search Console Indexing API for newly published URLs.
        Requires GOOGLE_INDEXING_KEY in secrets (optional — site will still index normally).
        Note: This is a 'nice to have' — new pages will be indexed regardless via sitemap.
        """
        if not self.site_domain or self.site_domain == "your-domain.com":
            logger.info("Site domain not set — skipping Google ping")
            return

        for slug in slugs:
            url = f"https://{self.site_domain}/posts/{slug}/"
            try:
                # Ping sitemap URL — free, no API key needed
                ping_url = f"https://www.google.com/ping?sitemap=https://{self.site_domain}/sitemap.xml"
                resp = requests.get(ping_url, timeout=10)
                logger.info(f"Google sitemap ping: {resp.status_code}")
                break  # Only need to ping once per run, not per article
            except Exception as e:
                logger.warning(f"Google ping failed (non-critical): {e}")
            time.sleep(1)

    def update_run_stats(self, articles_published: int) -> None:
        """Update state with this run's stats."""
        state = self._load_state()
        total = state.get("articles_published", 0) + articles_published
        self._save_state({
            "articles_published": total,
            "last_publish_run": datetime.now(timezone.utc).isoformat(),
            "published_this_run": self.published_this_run,
        })
        logger.info(f"Publisher: total articles on site: {total}")
