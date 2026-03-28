"""
Scanner Agent — fetches Google Local Pack results via SerpAPI (primary)
with automatic fallback to ValueSERP when the monthly SerpAPI quota is near exhausted.

Monthly search budget:
  - SerpAPI   : 245 searches (free tier cap 250, buffer of 5)
  - ValueSERP : 95 searches  (free tier cap 100, buffer of 5)
  - Total     : 340 searches/month combined

Provider selection logic (per calendar month):
  1. Use SerpAPI until SERPAPI_MONTHLY_LIMIT is reached.
  2. Automatically switch to ValueSERP for the remainder of the month.
  3. If both quotas are exhausted, log a warning and return an empty result
     (pipeline continues without crashing).
  4. Usage counts reset on the 1st of each calendar month.

Usage is persisted to data/search_usage.json so counts survive between
pipeline runs (GitHub Actions cold-starts each time).
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

SERPAPI_URL    = "https://serpapi.com/search.json"
VALUESERP_URL  = "https://api.valueserp.com/search"

USAGE_FILE_NAME = "search_usage.json"


class ScannerAgent:
    def __init__(
        self,
        api_key: str,                   # SerpAPI key (primary)
        valueserp_key: str = "",        # ValueSERP key (fallback)
        serpapi_monthly_limit: int = 245,
        valueserp_monthly_limit: int = 95,
        max_retries: int = 3,
        usage_file: Path | None = None,
    ):
        self.serpapi_key              = api_key
        self.valueserp_key            = valueserp_key
        self.serpapi_monthly_limit    = serpapi_monthly_limit
        self.valueserp_monthly_limit  = valueserp_monthly_limit
        self.max_retries              = max_retries

        # usage_file lives in the same data/ dir as other state files
        if usage_file is None:
            usage_file = Path(__file__).parent.parent / "data" / USAGE_FILE_NAME
        self.usage_file = usage_file

    # ── Usage tracking ────────────────────────────────────────────────────────

    def _current_month(self) -> str:
        """Return YYYY-MM string for the current UTC month."""
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def _load_usage(self) -> dict:
        """Load usage counters, resetting if a new calendar month has started."""
        current_month = self._current_month()
        try:
            with open(self.usage_file) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}

        # Reset counters if we've rolled into a new month
        if data.get("month") != current_month:
            logger.info(
                f"Scanner: new month ({current_month}) — resetting search usage counters"
            )
            data = {"month": current_month, "serpapi": 0, "valueserp": 0}
            self._save_usage(data)

        return data

    def _save_usage(self, data: dict) -> None:
        try:
            self.usage_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.usage_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Scanner: could not save usage file: {e}")

    def _increment_usage(self, provider: str) -> None:
        data = self._load_usage()
        data[provider] = data.get(provider, 0) + 1
        self._save_usage(data)

    def _choose_provider(self) -> str | None:
        """
        Decide which provider to use for the next search.
        Returns 'serpapi', 'valueserp', or None if both are exhausted.
        """
        usage = self._load_usage()
        serpapi_used   = usage.get("serpapi",   0)
        valueserp_used = usage.get("valueserp", 0)

        if serpapi_used < self.serpapi_monthly_limit and self.serpapi_key:
            remaining_serp = self.serpapi_monthly_limit - serpapi_used
            remaining_vs   = self.valueserp_monthly_limit - valueserp_used
            logger.debug(
                f"Scanner: SerpAPI {serpapi_used}/{self.serpapi_monthly_limit} used "
                f"| ValueSERP {valueserp_used}/{self.valueserp_monthly_limit} used "
                f"| Using SerpAPI ({remaining_serp} left this month)"
            )
            return "serpapi"

        if valueserp_used < self.valueserp_monthly_limit and self.valueserp_key:
            remaining = self.valueserp_monthly_limit - valueserp_used
            logger.info(
                f"Scanner: SerpAPI quota reached ({serpapi_used} searches used). "
                f"Switching to ValueSERP ({remaining} searches remaining this month)."
            )
            return "valueserp"

        logger.warning(
            f"Scanner: BOTH providers exhausted for {self._current_month()}. "
            f"SerpAPI: {serpapi_used}/{self.serpapi_monthly_limit}, "
            f"ValueSERP: {valueserp_used}/{self.valueserp_monthly_limit}. "
            f"Skipping search — quotas reset on the 1st of next month."
        )
        return None

    # ── Provider-specific search calls ────────────────────────────────────────

    def _search_serpapi(self, search_query: str) -> list[dict]:
        """Call SerpAPI google_maps engine and normalize the response."""
        params = {
            "engine":  "google_maps",
            "q":       search_query,
            "type":    "search",
            "api_key": self.serpapi_key,
        }
        resp = requests.get(SERPAPI_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return self._normalize_serpapi(data)

    def _search_valueserp(self, search_query: str) -> list[dict]:
        """Call ValueSERP places search and normalize the response."""
        params = {
            "api_key":     self.valueserp_key,
            "q":           search_query,
            "search_type": "places",   # ValueSERP uses 'places' for Google Maps/local results
        }
        resp = requests.get(VALUESERP_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return self._normalize_valueserp(data)

    # ── Response normalizers ──────────────────────────────────────────────────

    def _normalize_serpapi(self, data: dict) -> list[dict]:
        """Extract the fields we care about from a SerpAPI google_maps response."""
        results = []
        for i, place in enumerate(data.get("local_results", [])[:20], 1):
            results.append({
                "rank":        i,
                "name":        place.get("title", ""),
                "place_id":    place.get("place_id", ""),
                "rating":      place.get("rating", 0),
                "reviews":     place.get("reviews", 0),
                "address":     place.get("address", ""),
                "phone":       place.get("phone", ""),
                "website":     place.get("website", ""),
                "type":        place.get("type", ""),
                "thumbnail":   place.get("thumbnail", ""),
                "hours":       place.get("hours", ""),
                "description": place.get("description", ""),
            })
        return results

    def _normalize_valueserp(self, data: dict) -> list[dict]:
        """
        Extract the fields we care about from a ValueSERP places response.
        ValueSERP returns places_results[] with different field names than SerpAPI;
        we map them to the same schema so the rest of the pipeline is completely
        unaware of which provider was used.

        ValueSERP field mapping:
          places_results  → list of places (not 'local_results')
          title           → name
          data_cid        → place_id
          rating          → rating
          reviews         → reviews
          address         → address
          phone           → phone
          link            → website (not 'website')
          category        → type (not 'type')
          thumbnail       → thumbnail
          hours           → hours
          description     → description
          position        → rank (1-based, provided by ValueSERP)
        """
        results = []
        for i, place in enumerate(data.get("places_results", [])[:20], 1):
            results.append({
                "rank":        place.get("position", i),
                "name":        place.get("title", ""),
                "place_id":    place.get("data_cid", ""),
                "rating":      float(place.get("rating", 0) or 0),
                "reviews":     int(place.get("reviews", 0) or 0),
                "address":     place.get("address", ""),
                "phone":       place.get("phone", ""),
                "website":     place.get("link", ""),
                "type":        place.get("category", ""),
                "thumbnail":   place.get("thumbnail", ""),
                "hours":       place.get("hours", ""),
                "description": place.get("description", ""),
            })
        return results

    # ── Public interface ──────────────────────────────────────────────────────

    def scan_local_pack(self, search_query: str, location: str = "") -> list[dict]:
        """
        Search Google Maps rankings for a single query.
        Automatically selects the right provider based on monthly quota usage.
        Returns list of businesses with rank, rating, reviews, etc.
        """
        provider = self._choose_provider()
        if provider is None:
            return []  # both quotas exhausted — skip gracefully

        for attempt in range(self.max_retries):
            try:
                if provider == "serpapi":
                    results = self._search_serpapi(search_query)
                else:
                    results = self._search_valueserp(search_query)

                self._increment_usage(provider)
                logger.info(
                    f"Scanner [{provider}]: {len(results)} results for '{search_query}'"
                )
                return results

            except requests.RequestException as e:
                wait = (2 ** attempt) * 5
                logger.warning(
                    f"Scanner [{provider}] error (attempt {attempt + 1}/{self.max_retries}): "
                    f"{e}. Retrying in {wait}s…"
                )
                time.sleep(wait)

        logger.error(
            f"Scanner [{provider}]: failed after {self.max_retries} attempts for '{search_query}'"
        )
        return []

    def scan_all_targets(self, cities_data: dict) -> dict:
        """
        Scan all city + category combinations.
        Returns: {
            "austin_tx_plumber": [list of businesses],
            "austin_tx_dentist": [list of businesses],
            ...
        }
        """
        all_results = {}

        for city_config in cities_data.get("targets", []):
            city  = city_config["city"].lower()
            state = city_config["state"].lower()

            for cat in city_config.get("categories", []):
                key      = f"{city}_{state}_{cat['keyword']}"
                query    = cat["search_query"]
                location = f"{city_config['city']}, {city_config['state']}"

                logger.info(f"Scanner: scanning '{query}' in {location}")
                results = self.scan_local_pack(query, location)
                all_results[key] = results

                # Rate limit: 1 request per 2 seconds
                time.sleep(2)

        # Log end-of-run usage summary
        usage = self._load_usage()
        logger.info(
            f"Scanner: monthly usage after this run — "
            f"SerpAPI {usage.get('serpapi', 0)}/{self.serpapi_monthly_limit}, "
            f"ValueSERP {usage.get('valueserp', 0)}/{self.valueserp_monthly_limit}"
        )

        return all_results
