"""
Feed Agent — pulls and filters RSS articles for the newsletter.

Responsibilities:
  1. Fetch items from each RSS source in feeds.json
  2. Filter to only items published in the last 7 days
  3. Deduplicate against last week's URLs (from state.json)
  4. Score/rank items by relevance to our niche
  5. Return top N items for the Content Agent

Self-correction: if a feed URL is broken, logs and skips it. Never crashes.
"""
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import feedparser

logger = logging.getLogger(__name__)


class FeedAgent:
    def __init__(self, feeds_file: Path, state_file: Path, niche: str):
        self.feeds_file = feeds_file
        self.state_file = state_file
        self.niche = niche
        self._load_state()

    # ── State Management ────────────────────────────────────────────────────────
    def _load_state(self) -> None:
        try:
            with open(self.state_file) as f:
                self.state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.state = {"last_published_urls": []}

    def _save_state(self, new_urls: list[str]) -> None:
        self.state["last_published_urls"] = new_urls[-100:]  # keep last 100 URLs
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2)

    # ── Feed Fetching ───────────────────────────────────────────────────────────
    def _fetch_feed(self, source: dict[str, Any]) -> list[dict]:
        """Fetch a single RSS feed with error handling."""
        try:
            feed = feedparser.parse(source["url"])
            if feed.bozo and not feed.entries:
                logger.warning(f"Feed error for {source['name']}: {feed.bozo_exception}")
                return []

            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            items = []

            for entry in feed.entries:
                # Parse published date
                pub_date = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    pub_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)

                if pub_date and pub_date < cutoff:
                    continue  # too old

                url = entry.get("link", "")
                if not url or url in self.state.get("last_published_urls", []):
                    continue  # already featured

                summary = entry.get("summary", entry.get("description", ""))
                title = entry.get("title", "Untitled")

                items.append({
                    "title": title,
                    "url": url,
                    "summary": summary[:500],  # truncate for token efficiency
                    "source": source["name"],
                    "weight": source.get("weight", 1.0),
                    "pub_date": pub_date.isoformat() if pub_date else None,
                })

            logger.info(f"  {source['name']}: fetched {len(items)} fresh items")
            return items

        except Exception as e:
            logger.error(f"Failed to fetch {source['name']}: {e}")
            return []  # ← self-correction: skip broken feed, don't crash

    # ── Relevance Scoring ───────────────────────────────────────────────────────
    def _score_item(self, item: dict) -> float:
        """Score relevance to our niche using keyword matching."""
        ai_keywords = [
            "ai", "artificial intelligence", "llm", "gpt", "claude", "gemini",
            "automation", "productivity", "tool", "workflow", "agent", "prompt",
            "openai", "anthropic", "saas", "startup", "revenue", "solopreneur",
            "creator", "indie hacker", "passive income", "no-code", "low-code",
        ]
        text = (item["title"] + " " + item["summary"]).lower()
        keyword_hits = sum(1 for kw in ai_keywords if kw in text)
        source_weight = item.get("weight", 1.0)
        return keyword_hits * source_weight

    # ── Public Interface ────────────────────────────────────────────────────────
    def get_top_items(self, max_items: int = 8) -> list[dict]:
        """Fetch all feeds, score, deduplicate, return top items."""
        with open(self.feeds_file) as f:
            feeds_config = json.load(f)

        all_items = []
        for source in feeds_config["sources"]:
            items = self._fetch_feed(source)
            all_items.extend(items)
            time.sleep(0.5)  # polite crawl delay

        # Score and sort
        for item in all_items:
            item["score"] = self._score_item(item)

        all_items.sort(key=lambda x: x["score"], reverse=True)

        # Deduplicate by URL
        seen_urls = set()
        unique_items = []
        for item in all_items:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                unique_items.append(item)

        top = unique_items[:max_items]
        logger.info(f"Feed Agent: selected {len(top)} items from {len(all_items)} total")

        # Update state with these URLs so they won't be reused
        new_urls = self.state.get("last_published_urls", []) + [i["url"] for i in top]
        self._save_state(new_urls)

        return top
