---
title: Growth Accelerators — One-Time Setup
type: feat
status: active
date: 2026-03-21
---

# Growth Accelerators — One-Time Setup

## Overview

Both businesses are live and generating content automatically. This plan covers the 5 one-time actions from `MASTER_SETUP.md` (Growth Accelerators section) that compound passively — done once, benefit forever. None require ongoing effort.

**Current state:**
- `sutraflow.org` — 4 articles live, deploying via GitHub Actions + Cloudflare Pages
- Newsletter — 2 issues sent, 2 subscribers on Kit
- IndexNow already wired (new articles auto-submitted to Bing/Yandex on deploy)
- Sitemap live at `https://sutraflow.org/sitemap.xml`

---

## The 5 Actions

### Action 1: Google Search Console — Submit Sitemap

**Why:** Google won't index your articles without this. Free traffic starts here.

**Steps:**
1. Go to https://search.google.com/search-console/
2. Click **"Add property"** → choose **"URL prefix"** → enter `https://sutraflow.org`
3. Verify ownership — choose **"HTML tag"** method → copy the `<meta name="google-site-verification" ...>` tag
4. Tell Claude Code the tag value — it will add it to `layouts/partials/extend_head.html` (same file as the Impact verification tag) and push
5. Back in Search Console → click **Verify**
6. After verification: **Sitemaps** → **Add a new sitemap** → enter `sitemap.xml` → Submit

**Result:** Google begins crawling your 4 articles within 1–7 days. Traffic starts appearing in Search Console within 4–8 weeks.

---

### Action 2: Bing Webmaster Tools — Submit Sitemap

**Why:** Bing + DuckDuckGo combined = ~15% of search traffic. Free, 10-minute setup.

**Steps:**
1. Go to https://www.bing.com/webmasters/
2. Sign in with Microsoft account → **Add a site** → enter `https://sutraflow.org`
3. Verify ownership — choose **"XML file"** method → download the XML file Bing provides
4. Tell Claude Code the filename + content — it will add it to `business2_seo/hugo_site/static/` and push
5. Back in Bing Webmaster → click **Verify**
6. After verification: **Sitemaps** → **Submit sitemap** → enter `https://sutraflow.org/sitemap.xml`

**Note:** IndexNow is already set up in the deploy workflow, so new articles are already auto-pinging Bing on each Sunday run. This step gets your existing 4 articles indexed too.

---

### Action 3: Kit Recommendations — Enable Cross-Newsletter Growth

**Why:** Kit's Recommendations feature auto-grows your list through cross-promotion with other newsletters in the Kit ecosystem. Completely passive — Kit does the matching.

**Steps:**
1. Log into https://app.kit.com
2. Go to **Settings → Recommendations**
3. Enable "Recommend other creators to my subscribers"
4. Enable "Allow other creators to recommend me"
5. Set your newsletter category: **Technology / AI**
6. Add 2–3 newsletters you'd recommend (Kit will suggest similar ones)

**Result:** When subscribers join other Kit newsletters, your newsletter gets recommended — and vice versa. Each recommendation can bring 5–50 new subscribers/month passively.

---

### Action 4: Reddit One-Time Post — Newsletter Landing Page

**Why:** r/SideProject and r/Entrepreneur have high overlap with AI-curious professionals. A single honest post about your newsletter can drive 50–500 signups.

**Prep (do this first — tell Claude Code to help draft):**
- You need a Kit landing page URL for the newsletter signup
- In Kit: **Landing Pages** → **New Landing Page** → create simple signup page → publish → copy URL

**Post template (adapt and post manually):**

```
Title: "I built an AI newsletter that writes and sends itself — here's how"

Body:
I spent a weekend building a zero-touch newsletter about AI tools.
Every Tuesday morning it:
- Scrapes the latest AI tool launches from Product Hunt + TechCrunch
- Picks the 5 most interesting ones using Claude
- Writes a 600-word newsletter
- Sends it automatically via ConvertKit

I'm not involved at all. It just... runs.

If you're curious about AI tools for your work, I'm sharing what it discovers:
[YOUR KIT LANDING PAGE URL]

Happy to answer questions about the technical setup too.
```

**Post in:**
- https://www.reddit.com/r/SideProject/
- https://www.reddit.com/r/Entrepreneur/
- https://www.reddit.com/r/artificial/ (optional)

**Rules:** Be genuine, don't spam, answer comments. Post once and never again — the point is organic discovery.

---

### Action 5: Cross-Promotion — Link Newsletter → SEO Articles

**Why:** Every newsletter link to `sutraflow.org` is a backlink that builds domain authority. Also drives direct traffic to affiliate CTAs on the site.

**This is already partially built** — the newsletter content agent generates content from the same AI tool topics the SEO site covers. But explicit cross-links need to be wired.

**What to add to `business1_newsletter/config.py`:**

```python
# Add to newsletter footer template
SITE_CROSS_LINK = {
    "enabled": True,
    "text": "📖 Deep-dive reviews at sutraflow.org",
    "url": "https://sutraflow.org"
}
```

Or simpler: just confirm with Claude Code that the newsletter template already includes the site URL in the footer. Check `business1_newsletter/agents/` for the publisher agent template.

---

## Acceptance Criteria

- [ ] Google Search Console: property verified, sitemap `sitemap.xml` submitted
- [ ] Bing Webmaster Tools: property verified, sitemap `https://sutraflow.org/sitemap.xml` submitted
- [ ] Kit Recommendations: enabled and category set to Technology/AI
- [ ] Kit landing page: created and URL copied for Reddit post
- [ ] Reddit: post published in at least r/SideProject
- [ ] Cross-promotion: confirmed newsletter footer links to `sutraflow.org`

## Success Metrics

| Metric | Week 2 | Week 4 | Week 8 |
|---|---|---|---|
| Google Search Console impressions | >0 | >100 | >1,000 |
| Newsletter subscribers | >10 | >50 | >200 |
| sutraflow.org monthly sessions | >50 | >500 | >2,000 |

---

## Dependencies & Risks

| Risk | Mitigation |
|---|---|
| Google site verification requires adding a meta tag | Claude Code handles file edit + push |
| Bing verification may require an XML file in static/ | Claude Code handles file creation + push |
| Reddit post gets removed | Post must be genuine, not promotional spam — the technical angle helps |
| Kit landing page URL needed before Reddit post | Create Kit page first (Step 4 prep) |

---

## Sources & References

- Growth accelerators list: `MASTER_SETUP.md` (lines 219–229)
- Current sitemap URL: `https://sutraflow.org/sitemap.xml` (per `business2_seo/hugo_site/static/robots.txt`)
- IndexNow already wired: `.github/workflows/seo_weekly.yml` (last step)
- Verification tags go in: `business2_seo/hugo_site/layouts/partials/extend_head.html`
- Google Search Console: https://search.google.com/search-console/
- Bing Webmaster: https://www.bing.com/webmasters/
- Kit Recommendations: https://app.kit.com → Settings → Recommendations
