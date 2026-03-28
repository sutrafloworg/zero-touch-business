"""
Internal Linker — adds contextual internal links between published articles.

Scans a new article for mentions of topics covered by existing articles
and inserts markdown links at the first mention. Builds topical authority
and improves site structure for SEO.

Rules:
  - Maximum 5 internal links per article
  - Only link at first mention of each topic
  - Don't link within headings
  - Don't link inside existing markdown links
  - Case-insensitive matching
"""
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


class InternalLinker:
    def __init__(self, content_dir: Path):
        self.content_dir = content_dir

    def _load_existing_articles(self) -> list[dict]:
        """Load slug, title, and keywords from all published articles."""
        articles = []
        for md_file in sorted(self.content_dir.glob("*.md")):
            try:
                text = md_file.read_text(encoding="utf-8")
                # Parse frontmatter
                if text.startswith("---"):
                    parts = text.split("---", 2)
                    if len(parts) >= 3:
                        fm = parts[1]
                        slug = self._extract_fm(fm, "slug")
                        title = self._extract_fm(fm, "title")
                        if slug and title:
                            articles.append({
                                "slug": slug,
                                "title": title,
                                "file": md_file.name,
                            })
            except Exception:
                continue
        return articles

    def _extract_fm(self, frontmatter: str, key: str) -> str:
        """Extract a value from YAML frontmatter text."""
        for line in frontmatter.split("\n"):
            if line.strip().startswith(f"{key}:"):
                val = line.split(":", 1)[1].strip().strip('"').strip("'")
                return val
        return ""

    def _build_link_targets(self, articles: list[dict], current_slug: str) -> list[dict]:
        """Build list of (phrase, url) targets from existing articles, excluding current."""
        targets = []
        for art in articles:
            if art["slug"] == current_slug:
                continue

            title = art["title"]
            url = f"/posts/{art['slug']}/"

            # Use title as the primary match phrase
            targets.append({"phrase": title, "url": url})

            # Also generate shorter match phrases from title
            # e.g. "Best AI SEO Tools 2026" → "AI SEO tools"
            words = title.lower().split()
            if len(words) >= 4:
                # Try middle portion (skip "best" / year at ends)
                core = " ".join(w for w in words if w not in ("best", "2026", "2025", "top", "the"))
                if len(core) > 8:
                    targets.append({"phrase": core, "url": url})

        # Sort by phrase length descending (match longer phrases first)
        targets.sort(key=lambda t: len(t["phrase"]), reverse=True)
        return targets

    def add_internal_links(self, content: str, current_slug: str) -> str:
        """Add internal links to article content. Returns modified content."""
        articles = self._load_existing_articles()
        if len(articles) < 2:
            return content  # not enough articles to link between

        targets = self._build_link_targets(articles, current_slug)
        if not targets:
            return content

        # Split into frontmatter and body
        parts = content.split("---", 2)
        if len(parts) < 3:
            return content

        frontmatter = parts[1]
        body = parts[2]

        links_added = 0
        linked_urls = set()

        for target in targets:
            if links_added >= 5:
                break
            if target["url"] in linked_urls:
                continue

            phrase = target["phrase"]
            url = target["url"]

            # Find first mention in body text (case-insensitive)
            # Skip if inside a heading (##), existing link, or code block
            pattern = re.compile(
                r"(?<!\[)"           # not already inside a link [
                r"(?<!#\s)"          # not in a heading
                r"(?<!`)"            # not in inline code
                rf"({re.escape(phrase)})"
                r"(?!\])"            # not already a link text
                r"(?!`)",            # not in inline code
                re.IGNORECASE,
            )

            match = pattern.search(body)
            if match:
                # Only link if not inside a heading line
                line_start = body.rfind("\n", 0, match.start())
                line = body[line_start:match.start()]
                if "#" in line:
                    continue

                original = match.group(1)
                replacement = f"[{original}]({url})"
                # Replace only the first occurrence
                body = body[:match.start()] + replacement + body[match.end():]
                links_added += 1
                linked_urls.add(url)
                logger.debug(f"Internal link: '{original}' → {url}")

        if links_added > 0:
            logger.info(f"Internal linker: added {links_added} links to {current_slug}")

        return f"---{frontmatter}---{body}"
