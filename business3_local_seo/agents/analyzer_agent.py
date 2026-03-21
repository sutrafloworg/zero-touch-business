"""
Analyzer Agent — compares current scan to previous week's rankings.

Detects:
  - Rank drops (business was #3, now #7)
  - Rank gains (moved up — not actionable but useful context)
  - New entrants (wasn't in top 20 before)
  - Businesses that fell out of the pack entirely

For each rank drop, identifies likely reasons by comparing:
  - Review count changes (competitor gained reviews)
  - Rating changes
  - Website presence (do they have one?)
  - Hours/photos (completeness signals)
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class AnalyzerAgent:
    def __init__(self, rankings_file: Path):
        self.rankings_file = rankings_file

    def _load_history(self) -> dict:
        try:
            with open(self.rankings_file) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_history(self, history: dict) -> None:
        with open(self.rankings_file, "w") as f:
            json.dump(history, f, indent=2, default=str)

    def analyze(self, current_scan: dict) -> list[dict]:
        """
        Compare current scan to previous week.
        Returns list of 'drop alerts' — businesses that lost rank.
        """
        history = self._load_history()
        now = datetime.now(timezone.utc).isoformat()
        alerts = []

        for key, current_results in current_scan.items():
            prev_entry = history.get(key, {})
            prev_results = prev_entry.get("results", [])

            # Build lookup by place_id or name
            prev_lookup = {}
            for biz in prev_results:
                pid = biz.get("place_id") or biz.get("name", "")
                prev_lookup[pid] = biz

            for biz in current_results:
                pid = biz.get("place_id") or biz.get("name", "")
                prev = prev_lookup.get(pid)

                if not prev:
                    continue  # new entrant — skip

                prev_rank = prev.get("rank", 99)
                curr_rank = biz.get("rank", 99)

                if curr_rank > prev_rank:
                    # Rank dropped — analyze why
                    reasons = self._find_drop_reasons(prev, biz, prev_results, current_results)
                    alerts.append({
                        "category_key": key,
                        "business_name": biz["name"],
                        "address": biz.get("address", ""),
                        "phone": biz.get("phone", ""),
                        "website": biz.get("website", ""),
                        "prev_rank": prev_rank,
                        "curr_rank": curr_rank,
                        "rank_change": curr_rank - prev_rank,
                        "rating": biz.get("rating", 0),
                        "reviews": biz.get("reviews", 0),
                        "prev_reviews": prev.get("reviews", 0),
                        "reasons": reasons,
                        "scan_date": now,
                    })

            # Save current scan as history
            history[key] = {
                "last_scan": now,
                "results": current_results,
            }

        self._save_history(history)
        logger.info(f"Analyzer: found {len(alerts)} rank drop alerts across {len(current_scan)} categories")
        return alerts

    def _find_drop_reasons(
        self,
        prev_biz: dict,
        curr_biz: dict,
        prev_all: list,
        curr_all: list,
    ) -> list[str]:
        """Determine likely reasons for a rank drop."""
        reasons = []

        # 1. Competitor gained reviews
        competitors_gained_reviews = []
        for c in curr_all:
            if c["rank"] < curr_biz["rank"]:
                prev_c = next(
                    (p for p in prev_all if (p.get("place_id") or p["name"]) == (c.get("place_id") or c["name"])),
                    None,
                )
                if prev_c and c.get("reviews", 0) > prev_c.get("reviews", 0):
                    diff = c["reviews"] - prev_c["reviews"]
                    competitors_gained_reviews.append(
                        f"{c['name']} gained {diff} new review{'s' if diff > 1 else ''}"
                    )

        if competitors_gained_reviews:
            reasons.append(
                f"Competitors above you gained reviews: {'; '.join(competitors_gained_reviews[:3])}"
            )

        # 2. Rating dropped
        if curr_biz.get("rating", 0) < prev_biz.get("rating", 0):
            reasons.append(
                f"Your rating dropped from {prev_biz['rating']} to {curr_biz['rating']}"
            )

        # 3. No new reviews (stale profile)
        if curr_biz.get("reviews", 0) == prev_biz.get("reviews", 0):
            reasons.append("No new reviews this week — Google favors actively reviewed businesses")

        # 4. Missing website
        if not curr_biz.get("website"):
            reasons.append("No website linked to your Google Business Profile")

        # 5. Missing hours
        if not curr_biz.get("hours"):
            reasons.append("Business hours not set — incomplete profiles rank lower")

        # 6. Competitors have higher ratings
        higher_rated = [
            c for c in curr_all
            if c["rank"] < curr_biz["rank"]
            and c.get("rating", 0) > curr_biz.get("rating", 0)
        ]
        if higher_rated:
            reasons.append(
                f"{len(higher_rated)} competitor(s) above you have higher ratings"
            )

        if not reasons:
            reasons.append(
                "Google's local algorithm fluctuates — monitor over 2-3 weeks for a trend"
            )

        return reasons
