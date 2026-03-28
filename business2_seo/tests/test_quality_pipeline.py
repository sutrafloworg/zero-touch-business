"""
End-to-end tests for Content Quality Gate + E-E-A-T Hardening.

Tests quality scoring, revision flow, rejection flow, internal linking,
E-E-A-T frontmatter, and quality log writing — all without hitting the API.
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent to path so we can import agents
sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Sample test data ──────────────────────────────────────────────────────────

SAMPLE_ARTICLE = """---
title: "Best Ai Writing Tools 2026"
slug: "best-ai-writing-tools-2026"
date: 2026-03-27T12:00:00Z
lastmod: 2026-03-27T12:00:00Z
description: "I tested the top AI writing tools. After using each for 3 weeks, here are my honest picks."
keywords: ["best ai writing tools 2026", "ai tools", "review", "comparison"]
tags: ["review", "ai-tools"]
template: "listicle"
intent: "commercial"
draft: false
author: "Sutra Editorial"
author_url: "/about/"
reviewed_by: "Editorial Team"
last_fact_checked: "2026-03-27"
showToc: true
TocOpen: true
affiliate_disclosure: "This article contains affiliate links. We may earn a commission at no extra cost to you."
schema_type: "Article"
---

*Last tested and verified: March 2026. Pricing and features confirmed accurate as of this date.*

I tested 10 AI writing tools over the past month. Here are the ones worth your time.

## Why AI Writing Tools Matter in 2026

AI writing has evolved beyond simple text generation. Modern tools handle SEO optimization,
brand voice matching, and even fact-checking. After testing each tool for at least 3 weeks,
I can tell you which ones actually deliver on their promises.

## The Best AI Writing Tools: Quick Comparison

| Tool | Best For | Price | Rating |
|------|----------|-------|--------|
| Rytr | Budget writers | $9/mo | 4.2/5 |
| Jasper | Enterprise teams | $49/mo | 4.5/5 |
| Copy.ai | Marketing copy | $36/mo | 4.0/5 |

## Rytr: Best for Budget-Conscious Writers

I've been using Rytr since early 2025, and it remains my top pick for writers who need
quality output without breaking the bank. The UI loads in under 2 seconds, and the
tone selector actually works — not just a gimmick.

**Pros:**
- Incredibly affordable at $9/month
- 40+ use cases and templates
- Chrome extension works well

**Cons:**
- Output quality drops for technical content
- Limited team collaboration features

[Try Rytr Free →](https://rytr.me/?via=sutra)

## Jasper: Best for Enterprise Teams

Jasper's brand voice feature is what sets it apart. I ran the same brief through
Jasper and three competitors — Jasper was the only one that matched our style guide
consistently. Pricing verified March 2026.

**Pros:**
- Brand voice matching is excellent
- Team collaboration tools
- Extensive template library

**Cons:**
- Expensive for solo users
- Learning curve for full feature set

## How to Choose the Right Tool

Consider your budget first. If you're spending under $15/month, Rytr is your best bet.
For teams needing brand consistency, Jasper justifies its premium price.

## Frequently Asked Questions

### What's the best free AI writing tool?
Rytr offers a generous free tier with 10,000 characters per month.

### Can AI writing tools replace human writers?
No. They're best used as first-draft generators that humans then edit and refine.

### Are AI writing tools worth paying for?
If you write more than 5 articles per month, a paid tool will save you significant time.
"""

SAMPLE_KEYWORD = {
    "keyword": "best ai writing tools 2026",
    "slug": "best-ai-writing-tools-2026",
    "template": "listicle",
    "intent": "commercial",
    "primary_affiliate": "rytr",
    "secondary_affiliate": "notion",
}


# ── Test: Quality Agent scoring ───────────────────────────────────────────────

def test_quality_agent_scoring_pass():
    """Test that a good article scores above threshold and passes."""
    from agents.quality_agent import QualityAgent

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps({
        "structure": 8, "eeat": 8, "seo": 9, "readability": 8,
        "affiliate": 7, "originality": 7,
        "lowest_criteria": "affiliate",
        "revision_guidance": "All criteria met."
    }))]

    with patch("anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_response
        agent = QualityAgent(api_key="test-key", threshold=7)
        result = agent.score_article(SAMPLE_ARTICLE, SAMPLE_KEYWORD)

    assert result["passed"] is True, f"Expected pass, got: {result}"
    assert all(v >= 7 for v in result["scores"].values()), f"Scores below 7: {result['scores']}"
    print("  PASS: Quality scoring — good article passes")


def test_quality_agent_scoring_fail():
    """Test that a low-scoring article fails and provides revision guidance."""
    from agents.quality_agent import QualityAgent

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps({
        "structure": 8, "eeat": 5, "seo": 7, "readability": 8,
        "affiliate": 7, "originality": 6,
        "lowest_criteria": "eeat",
        "revision_guidance": "Add more first-person experience details."
    }))]

    with patch("anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_response
        agent = QualityAgent(api_key="test-key", threshold=7)
        result = agent.score_article(SAMPLE_ARTICLE, SAMPLE_KEYWORD)

    assert result["passed"] is False, f"Expected fail, got: {result}"
    assert result["lowest_criteria"] == "eeat"
    assert "experience" in result["revision_guidance"].lower()
    print("  PASS: Quality scoring — weak article fails with guidance")


def test_quality_agent_api_error_defaults_to_pass():
    """Test that API errors default to pass (don't block pipeline)."""
    from agents.quality_agent import QualityAgent

    with patch("anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.side_effect = Exception("API down")
        agent = QualityAgent(api_key="test-key", threshold=7)
        result = agent.score_article(SAMPLE_ARTICLE, SAMPLE_KEYWORD)

    assert result["passed"] is True, "API errors should default to pass"
    assert "error" in result
    print("  PASS: Quality scoring — API error defaults to pass")


# ── Test: Quality log writing ─────────────────────────────────────────────────

def test_quality_log_writing():
    """Test that quality metrics are appended to log file."""
    from agents.quality_agent import QualityAgent

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        f.write("[]")
        log_path = Path(f.name)

    try:
        with patch("anthropic.Anthropic"):
            agent = QualityAgent(api_key="test-key", log_file=log_path)

        agent.log_run({
            "articles_generated": 5,
            "articles_passed": 4,
            "articles_revised": 1,
            "articles_rejected": 0,
            "avg_scores": {"structure": 8.1, "eeat": 7.4},
            "rejected_keywords": [],
        })

        log_data = json.loads(log_path.read_text())
        assert len(log_data) == 1
        assert log_data[0]["articles_generated"] == 5
        assert log_data[0]["articles_passed"] == 4
        assert "date" in log_data[0]
        print("  PASS: Quality log writing works")
    finally:
        log_path.unlink(missing_ok=True)


# ── Test: Content Agent revision flow ─────────────────────────────────────────

def test_content_agent_revision():
    """Test that revision generates a new article based on feedback."""
    from agents.content_agent import ContentAgent

    revised_body = "## Revised Article\n\nI tested these tools extensively..." + " word" * 600

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=revised_body)]

    with patch("anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_response

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump({"tools": {
                "rytr": {"name": "Rytr", "affiliate_url": "https://rytr.me", "cta_text": "Try Rytr"},
                "notion": {"name": "Notion", "affiliate_url": "https://notion.so", "cta_text": "Try Notion"},
            }}, f)
            aff_path = Path(f.name)

        try:
            agent = ContentAgent(api_key="test-key", affiliate_file=aff_path)

            feedback = {
                "scores": {"structure": 8, "eeat": 5, "seo": 7, "readability": 8, "affiliate": 7, "originality": 6},
                "revision_guidance": "Add more first-person experience details and specific dates.",
            }

            revised, success = agent.revise_article(SAMPLE_ARTICLE, SAMPLE_KEYWORD, feedback)
            assert success is True, "Revision should succeed"
            assert "---" in revised, "Revised article should have frontmatter"
            assert "author:" in revised, "Revised article should have author in frontmatter"
            print("  PASS: Content agent revision flow works")
        finally:
            aff_path.unlink(missing_ok=True)


# ── Test: Content Agent deprecated affiliate filtering ────────────────────────

def test_deprecated_affiliate_filtered():
    """Test that _deprecated_ keys are filtered from affiliate loading."""
    from agents.content_agent import ContentAgent

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        json.dump({"tools": {
            "rytr": {"name": "Rytr", "affiliate_url": "https://rytr.me", "cta_text": "Try Rytr"},
            "notion": {"name": "Notion", "affiliate_url": "https://notion.so", "cta_text": "Try Notion"},
            "_deprecated_copy_ai": "removed — Copy.ai affiliate discontinued",
        }}, f)
        aff_path = Path(f.name)

    try:
        with patch("anthropic.Anthropic"):
            agent = ContentAgent(api_key="test-key", affiliate_file=aff_path)

        assert "_deprecated_copy_ai" not in agent.affiliates
        assert "rytr" in agent.affiliates
        assert len(agent.affiliates) == 2
        print("  PASS: Deprecated affiliates filtered correctly")
    finally:
        aff_path.unlink(missing_ok=True)


# ── Test: E-E-A-T frontmatter ────────────────────────────────────────────────

def test_eeat_frontmatter():
    """Test that _build_frontmatter includes E-E-A-T fields and correct schema types."""
    from agents.content_agent import ContentAgent

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        json.dump({"tools": {
            "rytr": {"name": "Rytr", "affiliate_url": "https://rytr.me", "cta_text": "Try Rytr"},
            "notion": {"name": "Notion", "affiliate_url": "https://notion.so", "cta_text": "Try Notion"},
        }}, f)
        aff_path = Path(f.name)

    try:
        with patch("anthropic.Anthropic"):
            agent = ContentAgent(api_key="test-key", affiliate_file=aff_path)

        # Test review template → Review schema
        kw_review = {**SAMPLE_KEYWORD, "template": "review"}
        fm = agent._build_frontmatter(kw_review, "Test description", "rytr")
        assert 'author: "Sutra Editorial"' in fm
        assert 'author_url: "/about/"' in fm
        assert 'reviewed_by: "Editorial Team"' in fm
        assert "last_fact_checked:" in fm
        assert 'schema_type: "Review"' in fm
        print("  PASS: E-E-A-T frontmatter — review gets Review schema")

        # Test tutorial template → HowTo schema
        kw_tutorial = {**SAMPLE_KEYWORD, "template": "tutorial"}
        fm = agent._build_frontmatter(kw_tutorial, "Test description", "rytr")
        assert 'schema_type: "HowTo"' in fm
        print("  PASS: E-E-A-T frontmatter — tutorial gets HowTo schema")

        # Test listicle template → Article schema
        kw_listicle = {**SAMPLE_KEYWORD, "template": "listicle"}
        fm = agent._build_frontmatter(kw_listicle, "Test description", "rytr")
        assert 'schema_type: "Article"' in fm
        print("  PASS: E-E-A-T frontmatter — listicle gets Article schema")
    finally:
        aff_path.unlink(missing_ok=True)


# ── Test: Internal Linker ─────────────────────────────────────────────────────

def test_internal_linker_adds_links():
    """Test that the internal linker adds contextual links between articles."""
    from agents.internal_linker import InternalLinker

    with tempfile.TemporaryDirectory() as tmpdir:
        content_dir = Path(tmpdir)

        # Create two "existing" articles
        (content_dir / "ai-seo-tools.md").write_text("""---
title: "Best AI SEO Tools 2026"
slug: "best-ai-seo-tools-2026"
---

Content about AI SEO tools.
""")

        (content_dir / "jasper-review.md").write_text("""---
title: "Jasper Review"
slug: "jasper-review-2026"
---

Content about Jasper.
""")

        linker = InternalLinker(content_dir=content_dir)

        # Test article that mentions AI SEO tools
        test_content = """---
title: "Best AI Writing Tools 2026"
slug: "best-ai-writing-tools-2026"
---

Here is a guide to AI writing tools.

If you also need AI SEO tools, check out our other guides.

Jasper Review is available as well.
"""

        result = linker.add_internal_links(test_content, "best-ai-writing-tools-2026")

        # Should NOT link to itself
        assert "/posts/best-ai-writing-tools-2026/" not in result

        # Should contain at least one internal link
        assert "[" in result.split("---", 2)[2], "Should have added at least one link"
        print("  PASS: Internal linker adds links to related articles")


def test_internal_linker_max_links():
    """Test that internal linker respects 5-link maximum."""
    from agents.internal_linker import InternalLinker

    with tempfile.TemporaryDirectory() as tmpdir:
        content_dir = Path(tmpdir)

        # Create 8 existing articles
        for i in range(8):
            (content_dir / f"article-{i}.md").write_text(f"""---
title: "Topic {i} Guide"
slug: "topic-{i}-guide"
---

Content about topic {i}.
""")

        linker = InternalLinker(content_dir=content_dir)

        # Article mentioning all 8 topics
        body_lines = [f"Learn about Topic {i} Guide here." for i in range(8)]
        test_content = f"""---
title: "Master Guide"
slug: "master-guide"
---

{''.join(body_lines)}
"""

        result = linker.add_internal_links(test_content, "master-guide")
        link_count = result.count("](/posts/")
        assert link_count <= 5, f"Expected max 5 links, got {link_count}"
        print(f"  PASS: Internal linker respects max 5 links (added {link_count})")


def test_internal_linker_no_link_in_heading():
    """Test that internal linker doesn't insert links inside headings."""
    from agents.internal_linker import InternalLinker

    with tempfile.TemporaryDirectory() as tmpdir:
        content_dir = Path(tmpdir)

        (content_dir / "jasper-review.md").write_text("""---
title: "Jasper Review"
slug: "jasper-review"
---
Content.
""")

        linker = InternalLinker(content_dir=content_dir)

        test_content = """---
title: "Test"
slug: "test-article"
---

## Jasper Review Section

Jasper Review is great for writers.
"""

        result = linker.add_internal_links(test_content, "test-article")
        body = result.split("---", 2)[2]

        # Check that the heading line does NOT contain a link
        for line in body.split("\n"):
            if line.strip().startswith("#"):
                assert "](/posts/" not in line, f"Link found in heading: {line}"

        print("  PASS: Internal linker doesn't link inside headings")


# ── Test: Revision instructions mapping ───────────────────────────────────────

def test_revision_instructions():
    """Test that failing criteria map to correct fix instructions."""
    from agents.content_agent import ContentAgent

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        json.dump({"tools": {
            "rytr": {"name": "Rytr", "affiliate_url": "https://rytr.me", "cta_text": "Try"},
            "notion": {"name": "Notion", "affiliate_url": "https://notion.so", "cta_text": "Try"},
        }}, f)
        aff_path = Path(f.name)

    try:
        with patch("anthropic.Anthropic"):
            agent = ContentAgent(api_key="test-key", affiliate_file=aff_path)

        # Test each failing criterion
        instructions = agent._revision_instructions({"eeat": 5, "seo": 4})
        assert "first-person" in instructions.lower()
        assert "keyword" in instructions.lower()

        instructions = agent._revision_instructions({"structure": 3})
        assert "heading" in instructions.lower()

        instructions = agent._revision_instructions({"readability": 4})
        assert "paragraph" in instructions.lower()

        instructions = agent._revision_instructions({})
        assert "general" in instructions.lower()

        print("  PASS: Revision instructions map correctly to failing criteria")
    finally:
        aff_path.unlink(missing_ok=True)


# ── Run all tests ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== Content Quality Pipeline Tests ===\n")
    tests = [
        test_quality_agent_scoring_pass,
        test_quality_agent_scoring_fail,
        test_quality_agent_api_error_defaults_to_pass,
        test_quality_log_writing,
        test_content_agent_revision,
        test_deprecated_affiliate_filtered,
        test_eeat_frontmatter,
        test_internal_linker_adds_links,
        test_internal_linker_max_links,
        test_internal_linker_no_link_in_heading,
        test_revision_instructions,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  FAIL: {test.__name__} — {e}")

    print(f"\n{'=' * 40}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    print(f"{'=' * 40}\n")
    sys.exit(1 if failed else 0)
