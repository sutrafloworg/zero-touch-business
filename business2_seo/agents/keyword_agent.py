"""
Keyword Agent — reads the keyword queue and returns the next batch to process.

Responsibilities:
  1. Read keywords.csv — find rows with status='pending'
  2. Skip keywords already processed (in state.json)
  3. Return next N keywords for content generation
  4. Mark them as 'in_progress' to prevent double-processing

Self-correction: If a keyword was marked 'in_progress' but no article exists,
it gets re-queued as 'pending' (handles interrupted runs).
"""
import csv
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class KeywordAgent:
    def __init__(self, keywords_file: Path, state_file: Path, content_dir: Path):
        self.keywords_file = keywords_file
        self.state_file = state_file
        self.content_dir = content_dir

    def _load_state(self) -> dict:
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"keywords_processed": []}

    def _save_state(self, updates: dict) -> None:
        state = self._load_state()
        state.update(updates)
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2, default=str)

    def _slug(self, keyword: str) -> str:
        """Convert keyword to URL-safe slug."""
        import re
        slug = keyword.lower().strip()
        slug = re.sub(r"[^a-z0-9\s-]", "", slug)
        slug = re.sub(r"[\s]+", "-", slug)
        return slug[:80]

    def _article_exists(self, keyword: str) -> bool:
        """Check if article was already generated."""
        slug = self._slug(keyword)
        article_path = self.content_dir / f"{slug}.md"
        return article_path.exists()

    def _heal_in_progress(self, rows: list[dict]) -> list[dict]:
        """
        Self-correction: if keyword is 'in_progress' but no article file exists,
        reset to 'pending' (handles crashed runs).
        """
        healed = []
        for row in rows:
            if row["status"] == "in_progress" and not self._article_exists(row["keyword"]):
                logger.warning(f"Healing stuck keyword: '{row['keyword']}' → pending")
                row["status"] = "pending"
            healed.append(row)
        return healed

    def _read_keywords(self) -> list[dict]:
        """Read all keywords from CSV."""
        with open(self.keywords_file, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def _write_keywords(self, rows: list[dict]) -> None:
        """Write updated rows back to CSV."""
        if not rows:
            return
        fieldnames = list(rows[0].keys())
        with open(self.keywords_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def get_next_batch(self, batch_size: int = 5) -> list[dict]:
        """
        Return next batch of pending keywords and mark them in_progress.
        Each returned dict has: keyword, intent, template, primary_affiliate,
                                secondary_affiliate, slug
        """
        rows = self._read_keywords()
        rows = self._heal_in_progress(rows)

        state = self._load_state()
        already_processed = set(state.get("keywords_processed", []))

        pending = [
            r for r in rows
            if r["status"] == "pending" and r["keyword"] not in already_processed
        ]

        if not pending:
            logger.info("Keyword Agent: no pending keywords remaining")
            return []

        batch = pending[:batch_size]
        batch_keywords = {r["keyword"] for r in batch}

        # Mark batch as in_progress in CSV
        for row in rows:
            if row["keyword"] in batch_keywords:
                row["status"] = "in_progress"

        self._write_keywords(rows)

        # Add slug to each keyword dict
        for kw in batch:
            kw["slug"] = self._slug(kw["keyword"])

        logger.info(f"Keyword Agent: {len(batch)} keywords selected for this run")
        return batch

    def mark_done(self, keyword: str, success: bool) -> None:
        """Update keyword status after content generation attempt."""
        rows = self._read_keywords()
        new_status = "done" if success else "failed"

        for row in rows:
            if row["keyword"] == keyword:
                row["status"] = new_status
                break

        self._write_keywords(rows)

        if success:
            state = self._load_state()
            processed = state.get("keywords_processed", [])
            processed.append(keyword)
            self._save_state({"keywords_processed": processed})

    def get_stats(self) -> dict:
        """Return keyword processing statistics."""
        rows = self._read_keywords()
        status_counts = {}
        for row in rows:
            status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
        return status_counts
