"""
Scanner Agent — fetches Google Local Pack results via SerpAPI.

For each city + category, returns:
  - Business name, rank position
  - Rating, review count
  - Address, phone, website
  - Whether they have photos, hours listed

SerpAPI free tier: 100 searches/month.
At 5 categories × 1 city × 4 weeks = 20 searches/month — well within limits.
"""
import json
import logging
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search.json"


class ScannerAgent:
    def __init__(self, api_key: str, max_retries: int = 3):
        self.api_key = api_key
        self.max_retries = max_retries

    def scan_local_pack(self, search_query: str, location: str) -> list[dict]:
        """
        Search Google local results via SerpAPI.
        Returns list of businesses with rank, rating, reviews, etc.
        """
        params = {
            "engine": "google_maps",
            "q": search_query,
            "ll": "",  # let SerpAPI geocode from the query
            "type": "search",
            "api_key": self.api_key,
        }

        for attempt in range(self.max_retries):
            try:
                resp = requests.get(SERPAPI_URL, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()

                results = []
                for i, place in enumerate(data.get("local_results", [])[:20], 1):
                    results.append({
                        "rank": i,
                        "name": place.get("title", ""),
                        "place_id": place.get("place_id", ""),
                        "rating": place.get("rating", 0),
                        "reviews": place.get("reviews", 0),
                        "address": place.get("address", ""),
                        "phone": place.get("phone", ""),
                        "website": place.get("website", ""),
                        "type": place.get("type", ""),
                        "thumbnail": place.get("thumbnail", ""),
                        "hours": place.get("hours", ""),
                        "description": place.get("description", ""),
                    })

                logger.info(f"Scanner: found {len(results)} results for '{search_query}'")
                return results

            except requests.RequestException as e:
                wait = (2 ** attempt) * 5
                logger.warning(f"SerpAPI error (attempt {attempt + 1}): {e}. Waiting {wait}s")
                time.sleep(wait)

        logger.error(f"Scanner: failed after {self.max_retries} attempts for '{search_query}'")
        return []

    def scan_all_targets(self, cities_data: dict) -> dict:
        """
        Scan all city+category combinations.
        Returns: {
            "austin_tx_plumber": [list of businesses],
            "austin_tx_dentist": [list of businesses],
            ...
        }
        """
        all_results = {}

        for city_config in cities_data.get("targets", []):
            city = city_config["city"].lower()
            state = city_config["state"].lower()

            for cat in city_config.get("categories", []):
                key = f"{city}_{state}_{cat['keyword']}"
                query = cat["search_query"]
                location = f"{city_config['city']}, {city_config['state']}"

                logger.info(f"Scanner: scanning '{query}' in {location}")
                results = self.scan_local_pack(query, location)
                all_results[key] = results

                # Rate limit: 1 request per 2 seconds
                time.sleep(2)

        return all_results
