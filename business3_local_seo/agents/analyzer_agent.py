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

Progressive insights — as more weeks of data accumulate, richer analysis unlocks:
  Week 1: Basic rank drop + reasons
  Week 2+: Review velocity (reviews/week)
  Week 3+: Rank trend (improving/declining/volatile)
  Week 4+: Competitor spotlight (who's climbing fastest)
  Week 6+: Category health score + market position summary
  Week 8+: Seasonal patterns and predictive alerts
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_HISTORY_WEEKS = 12  # Keep 12 weeks of snapshots per category


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

    def _get_snapshots(self, history_entry: dict) -> list[dict]:
        """Get the list of weekly snapshots from a history entry.
        Handles both old format (single 'results') and new format ('snapshots' list)."""
        if "snapshots" in history_entry:
            return history_entry["snapshots"]
        # Migrate old format: single results → one-element snapshot list
        if "results" in history_entry:
            return [{
                "date": history_entry.get("last_scan", ""),
                "results": history_entry["results"],
            }]
        return []

    def analyze(self, current_scan: dict) -> list[dict]:
        """
        Compare current scan to previous week.
        Returns list of 'drop alerts' — businesses that lost rank.
        Each alert includes progressive insights based on accumulated history.
        """
        history = self._load_history()
        now = datetime.now(timezone.utc).isoformat()
        alerts = []

        for key, current_results in current_scan.items():
            prev_entry = history.get(key, {})
            snapshots = self._get_snapshots(prev_entry)
            prev_results = snapshots[-1]["results"] if snapshots else []

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

                    # Build progressive insights from historical data
                    weeks_of_data = len(snapshots) + 1  # including this week
                    insights = self._build_progressive_insights(
                        pid, biz, snapshots, current_results, weeks_of_data
                    )

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
                        "weeks_tracked": weeks_of_data,
                        "insights": insights,
                    })

            # Append current scan as new snapshot, trim to MAX_HISTORY_WEEKS
            snapshots.append({"date": now, "results": current_results})
            if len(snapshots) > MAX_HISTORY_WEEKS:
                snapshots = snapshots[-MAX_HISTORY_WEEKS:]

            history[key] = {
                "last_scan": now,
                "results": current_results,  # keep for backward compat
                "snapshots": snapshots,
            }

        self._save_history(history)
        logger.info(f"Analyzer: found {len(alerts)} rank drop alerts across {len(current_scan)} categories")
        return alerts

    def _build_progressive_insights(
        self,
        place_id: str,
        current_biz: dict,
        snapshots: list[dict],
        current_all: list[dict],
        weeks_of_data: int,
    ) -> dict:
        """
        Build progressively richer insights based on how many weeks of data we have.
        More data = more interesting analysis = higher retention.
        """
        insights = {}

        if weeks_of_data < 2:
            return insights

        # --- Week 2+: Review velocity ---
        biz_history = self._extract_biz_history(place_id, snapshots)
        if len(biz_history) >= 2:
            first = biz_history[0]
            latest = biz_history[-1]
            weeks_span = max(len(biz_history) - 1, 1)
            review_diff = latest.get("reviews", 0) - first.get("reviews", 0)
            velocity = round(review_diff / weeks_span, 1)
            insights["review_velocity"] = {
                "reviews_per_week": velocity,
                "total_gained": review_diff,
                "over_weeks": weeks_span,
                "verdict": "strong" if velocity >= 3 else "moderate" if velocity >= 1 else "stagnant",
            }

        if weeks_of_data < 3:
            return insights

        # --- Week 3+: Rank trend ---
        rank_history = [h.get("rank", 99) for h in biz_history]
        if len(rank_history) >= 3:
            recent_avg = sum(rank_history[-3:]) / 3
            if len(rank_history) >= 6:
                older_avg = sum(rank_history[:3]) / 3
            else:
                older_avg = rank_history[0]
            trend_diff = recent_avg - older_avg
            if trend_diff > 1.5:
                trend = "declining"
            elif trend_diff < -1.5:
                trend = "improving"
            elif max(rank_history) - min(rank_history) > 4:
                trend = "volatile"
            else:
                trend = "stable"
            insights["rank_trend"] = {
                "direction": trend,
                "history": rank_history[-6:],  # last 6 weeks
                "best_rank": min(rank_history),
                "worst_rank": max(rank_history),
            }

        if weeks_of_data < 4:
            return insights

        # --- Week 4+: Competitor spotlight ---
        # Find which competitor gained the most ranks over the tracked period
        competitor_movements = []
        for comp in current_all:
            comp_pid = comp.get("place_id") or comp.get("name", "")
            if comp_pid == place_id:
                continue
            comp_history = self._extract_biz_history(comp_pid, snapshots)
            if len(comp_history) >= 2:
                first_rank = comp_history[0].get("rank", 99)
                curr_rank = comp.get("rank", 99)
                improvement = first_rank - curr_rank  # positive = moved up
                review_gain = comp.get("reviews", 0) - comp_history[0].get("reviews", 0)
                competitor_movements.append({
                    "name": comp["name"],
                    "rank_improvement": improvement,
                    "current_rank": curr_rank,
                    "review_gain": review_gain,
                    "rating": comp.get("rating", 0),
                })

        if competitor_movements:
            # Sort by rank improvement (biggest climber)
            competitor_movements.sort(key=lambda x: x["rank_improvement"], reverse=True)
            rising_star = competitor_movements[0] if competitor_movements[0]["rank_improvement"] > 0 else None
            if rising_star:
                insights["competitor_spotlight"] = {
                    "fastest_climber": rising_star["name"],
                    "climbed_positions": rising_star["rank_improvement"],
                    "their_review_gain": rising_star["review_gain"],
                    "their_rating": rising_star["rating"],
                    "their_current_rank": rising_star["current_rank"],
                }

        if weeks_of_data < 6:
            return insights

        # --- Week 6+: Category health score ---
        avg_reviews = sum(c.get("reviews", 0) for c in current_all[:10]) / min(len(current_all), 10)
        avg_rating = sum(c.get("rating", 0) for c in current_all[:10]) / min(len(current_all), 10)
        biz_review_pct = (current_biz.get("reviews", 0) / avg_reviews * 100) if avg_reviews else 0
        biz_rating_pct = (current_biz.get("rating", 0) / avg_rating * 100) if avg_rating else 0
        health_score = round((biz_review_pct * 0.6 + biz_rating_pct * 0.4) / 100 * 10, 1)
        health_score = min(max(health_score, 1), 10)  # clamp 1-10

        insights["category_health"] = {
            "score": health_score,
            "your_reviews": current_biz.get("reviews", 0),
            "category_avg_reviews": round(avg_reviews),
            "your_rating": current_biz.get("rating", 0),
            "category_avg_rating": round(avg_rating, 1),
            "position_summary": (
                "above average" if health_score >= 7
                else "competitive" if health_score >= 5
                else "needs attention"
            ),
        }

        return insights

    def _extract_biz_history(self, place_id: str, snapshots: list[dict]) -> list[dict]:
        """Extract a single business's data across all snapshots."""
        history = []
        for snap in snapshots:
            for biz in snap.get("results", []):
                pid = biz.get("place_id") or biz.get("name", "")
                if pid == place_id:
                    history.append(biz)
                    break
        return history

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
