"""
Scanner Agent — fetches Google Local Pack results via a rotary array of search APIs.

Provider rotation (exhausts each free tier before moving to next):
  1. SerpAPI     : 245 searches/month (free tier cap 250, buffer of 5)
  2. Outscraper  : 95 searches/month  (free tier ~100 Google Maps requests, buffer of 5)
  3. ValueSERP   : 95 searches/month  (free tier cap 100, buffer of 5)
  Total budget   : 435 searches/month combined

Provider selection logic (per calendar month):
  1. Use SerpAPI until SERPAPI_MONTHLY_LIMIT is reached.
  2. Switch to Outscraper for the next batch.
  3. If Outscraper is exhausted, fall back to ValueSERP.
  4. If all quotas are exhausted, log a warning and return an empty result.
  5. Usage counts reset on the 1st of each calendar month.

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

SERPAPI_URL     = "https://serpapi.com/search.json"
VALUESERP_URL  = "https://api.valueserp.com/search"
OUTSCRAPER_URL = "https://api.app.outscraper.com/maps/search-v3"

USAGE_FILE_NAME = "search_usage.json"


class ScannerAgent:
    def __init__(
        self,
        api_key: str,                        # SerpAPI key (primary)
        valueserp_key: str = "",             # ValueSERP key (fallback 2)
        outscraper_key: str = "",            # Outscraper key (fallback 1)
        serpapi_monthly_limit: int = 245,
        outscraper_monthly_limit: int = 95,
        valueserp_monthly_limit: int = 95,
        max_retries: int = 3,
        usage_file: Path | None = None,
    ):
        self.serpapi_key              = api_key
        self.valueserp_key            = valueserp_key
        self.outscraper_key           = outscraper_key
        self.serpapi_monthly_limit    = serpapi_monthly_limit
        self.outscraper_monthly_limit = outscraper_monthly_limit
        self.valueserp_monthly_limit  = valueserp_monthly_limit
        self.max_retries              = max_retries

        if usage_file is None:
            usage_file = Path(__file__).parent.parent / "data" / USAGE_FILE_NAME
        self.usage_file = usage_file

    # ── Usage tracking ────────────────────────────────────────────────────────

    def _current_month(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def _load_usage(self) -> dict:
        current_month = self._current_month()
        try:
            with open(self.usage_file) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}

        if data.get("month") != current_month:
            logger.info(
                f"Scanner: new month ({current_month}) — resetting search usage counters"
            )
            data = {"month": current_month, "serpapi": 0, "outscraper": 0, "valueserp": 0}
            self._save_usage(data)

        # Ensure outscraper key exists in older usage files
        if "outscraper" not in data:
            data["outscraper"] = 0

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
        Returns 'serpapi', 'outscraper', 'valueserp', or None if all exhausted.
        """
        usage = self._load_usage()
        serpapi_used    = usage.get("serpapi", 0)
        outscraper_used = usage.get("outscraper", 0)
        valueserp_used = usage.get("valueserp", 0)

        # Priority 1: SerpAPI
        if serpapi_used < self.serpapi_monthly_limit and self.serpapi_key:
            remaining = self.serpapi_monthly_limit - serpapi_used
            logger.debug(
                f"Scanner: SerpAPI {serpapi_used}/{self.serpapi_monthly_limit} used "
                f"| Using SerpAPI ({remaining} left)"
            )
            return "serpapi"

        # Priority 2: Outscraper
        if outscraper_used < self.outscraper_monthly_limit and self.outscraper_key:
            remaining = self.outscraper_monthly_limit - outscraper_used
            logger.info(
                f"Scanner: SerpAPI quota reached ({serpapi_used} used). "
                f"Switching to Outscraper ({remaining} searches remaining)."
            )
            return "outscraper"

        # Priority 3: ValueSERP
        if valueserp_used < self.valueserp_monthly_limit and self.valueserp_key:
            remaining = self.valueserp_monthly_limit - valueserp_used
            logger.info(
                f"Scanner: SerpAPI + Outscraper quotas reached. "
                f"Switching to ValueSERP ({remaining} searches remaining)."
            )
            return "valueserp"

        logger.warning(
            f"Scanner: ALL providers exhausted for {self._current_month()}. "
            f"SerpAPI: {serpapi_used}/{self.serpapi_monthly_limit}, "
            f"Outscraper: {outscraper_used}/{self.outscraper_monthly_limit}, "
            f"ValueSERP: {valueserp_used}/{self.valueserp_monthly_limit}. "
            f"Skipping search — quotas reset on the 1st of next month."
        )
        return None

    # ── Provider-specific search calls ────────────────────────────────────────

    def _search_serpapi(self, search_query: str) -> list[dict]:
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

    def _search_outscraper(self, search_query: str) -> list[dict]:
        """Call Outscraper Google Maps Search API v3 and normalize the response.

        Outscraper's free tier gives ~25 requests/month with 20 results each.
        We set a conservative limit and normalize to our standard schema.
        """
        headers = {
            "X-API-KEY": self.outscraper_key,
            "Accept": "application/json",
        }
        params = {
            "query": search_query,
            "limit": 20,          # top 20 results like SerpAPI
            "async": "false",     # synchronous request
        }
        resp = requests.get(OUTSCRAPER_URL, params=params, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return self._normalize_outscraper(data)

    def _search_valueserp(self, search_query: str) -> list[dict]:
        params = {
            "api_key":     self.valueserp_key,
            "q":           search_query,
            "search_type": "places",
        }
        resp = requests.get(VALUESERP_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return self._normalize_valueserp(data)

    # ── Response normalizers ──────────────────────────────────────────────────

    def _normalize_serpapi(self, data: dict) -> list[dict]:
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

    def _normalize_outscraper(self, data: dict) -> list[dict]:
        """
        Normalize Outscraper Google Maps Search v3 response.

        Outscraper returns a nested list: [[{place1}, {place2}, ...]]
        Each place has fields like:
          name, place_id, rating, reviews, full_address, phone, site,
          type, photo, working_hours, description, etc.
        """
        results = []
        # Outscraper wraps results in a list of lists
        places_list = data if isinstance(data, list) else data.get("data", [])
        places = places_list[0] if places_list and isinstance(places_list[0], list) else places_list

        for i, place in enumerate(places[:20], 1):
            if not isinstance(place, dict):
                continue
            results.append({
                "rank":        i,
                "name":        place.get("name", ""),
                "place_id":    place.get("place_id", ""),
                "rating":      float(place.get("rating", 0) or 0),
                "reviews":     int(place.get("reviews", 0) or 0),
                "address":     place.get("full_address", place.get("address", "")),
                "phone":       place.get("phone", ""),
                "website":     place.get("site", place.get("website", "")),
                "type":        place.get("type", place.get("subtypes", [""])[0] if place.get("subtypes") else ""),
                "thumbnail":   place.get("photo", ""),
                "hours":       place.get("working_hours", ""),
                "description": place.get("description", ""),
            })
        return results

    def _normalize_valueserp(self, data: dict) -> list[dict]:
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
        """
        provider = self._choose_provider()
        if provider is None:
            return []

        for attempt in range(self.max_retries):
            try:
                if provider == "serpapi":
                    results = self._search_serpapi(search_query)
                elif provider == "outscraper":
                    results = self._search_outscraper(search_query)
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
                    f"{e}. Retrying in {wait}s..."
                )
                time.sleep(wait)

        logger.error(
            f"Scanner [{provider}]: failed after {self.max_retries} attempts for '{search_query}'"
        )
        return []

    def scan_all_targets(self, cities_data: dict) -> dict:
        """Scan all city + category combinations."""
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

                time.sleep(2)  # rate limit

        usage = self._load_usage()
        logger.info(
            f"Scanner: monthly usage after this run — "
            f"SerpAPI {usage.get('serpapi', 0)}/{self.serpapi_monthly_limit}, "
            f"Outscraper {usage.get('outscraper', 0)}/{self.outscraper_monthly_limit}, "
            f"ValueSERP {usage.get('valueserp', 0)}/{self.valueserp_monthly_limit}"
        )

        return all_results
