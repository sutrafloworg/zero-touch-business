---
title: Revenue Tracking Dashboard — Weekly Automated Report
type: feat
status: completed
date: 2026-03-21
---

# Revenue Tracking Dashboard — Weekly Automated Report

## Overview

Both businesses generate data but none of it is surfaced automatically. This plan builds a weekly HTML email report (sent every Sunday after the SEO run) covering newsletter performance, site traffic, and affiliate click attribution — using APIs already available in the existing secrets.

**No new accounts. No new secrets needed.**

---

## Current State

| Data source | What exists | Gap |
|---|---|---|
| Kit newsletter stats | `get_broadcast_stats()` coded in `publisher_agent.py` | Never called in digest |
| Kit subscriber count | `get_subscriber_count()` coded | Only saved to state.json, not reported |
| Site traffic | Cloudflare Pages deployed | No analytics query anywhere |
| Affiliate clicks | Links in both JSON files | No UTM tracking, no click attribution |
| Stats history | `state.json` (basic) | No trend data, no week-over-week |

The `send_weekly_digest` in `monitor_agent.py` runs every 4th newsletter issue and sends a plain-text stub pointing to the Kit dashboard manually. This plan replaces it with a full automated HTML report.

---

## Proposed Solution

### Component 1: StatsAgent (`business2_seo/agents/stats_agent.py`)

Runs every Sunday after the SEO content run. Collects:

1. **Kit newsletter metrics** via existing API secret:
   - Active subscriber count
   - Latest broadcast: open rate, click rate, recipients
   - Week-over-week subscriber growth (vs. last stored snapshot)

2. **Cloudflare Analytics** via existing `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID`:
   - Weekly page views on `sutraflow.org`
   - Top 5 pages by views (which articles are getting traffic)
   - Uses Cloudflare GraphQL Analytics API

3. **SEO business stats** from local `state.json`:
   - Total articles published
   - Keywords processed
   - Last run status

4. **Trend storage** in `business2_seo/data/stats_history.json`:
   - Appends a snapshot each week
   - Enables week-over-week comparison

### Component 2: Enhanced Weekly Report Email

Replace the plain-text `send_weekly_digest` with a formatted HTML email:

```
Subject: 📊 Weekly Report — AI Tools Insider [Mar 21]

┌─────────────────────────────────────┐
│  NEWSLETTER                         │
│  Subscribers:    127  (+23 this wk) │
│  Open rate:      34%                │
│  Click rate:      8%                │
│  Issues sent:     6                 │
├─────────────────────────────────────┤
│  SEO SITE                           │
│  Page views:    1,240  this week    │
│  Top article:   ChatGPT vs Claude   │
│  Articles live:  24                 │
│  New this week:   5                 │
├─────────────────────────────────────┤
│  AFFILIATE LINKS                    │
│  Track at: Impact / PartnerStack    │
│  (links below)                      │
└─────────────────────────────────────┘
```

### Component 3: UTM Parameters on Affiliate Links

Add `utm_source`, `utm_medium`, `utm_campaign` to all affiliate URLs so Cloudflare Analytics shows which articles drive affiliate clicks.

**`business1_newsletter/data/affiliate_links.json`** — add `utm_medium=email`:
```json
"affiliate_url": "https://writesonic.com/?via=YOUR_REF_ID&utm_source=sutraflow&utm_medium=email"
```

**`business2_seo/data/affiliate_links.json`** — add `utm_medium=organic`:
```json
"affiliate_url": "https://writesonic.com/?via=YOUR_REF_ID&utm_source=sutraflow&utm_medium=organic&utm_campaign=seo"
```

This makes affiliate clicks visible in Cloudflare outbound analytics and lets you see which traffic source converts better.

### Component 4: Wire StatsAgent into SEO Orchestrator

Add to `business2_seo/orchestrator.py` after the publish step:

```python
# business2_seo/orchestrator.py
from agents.stats_agent import StatsAgent

stats_agent = StatsAgent(
    kit_api_secret=config.KIT_API_SECRET,
    kit_last_broadcast_id=state.get("last_broadcast_id"),
    cf_api_token=config.CF_API_TOKEN,
    cf_account_id=config.CF_ACCOUNT_ID,
    site_domain=config.SITE_DOMAIN,
    stats_file=config.STATS_HISTORY_FILE,
    alert_email=config.ALERT_EMAIL,
    gmail_user=config.GMAIL_USER,
    gmail_app_password=config.GMAIL_APP_PASSWORD,
)
stats_agent.run_and_report()
```

---

## Files to Create / Modify

| File | Action |
|---|---|
| `business2_seo/agents/stats_agent.py` | **Create** — StatsAgent class |
| `business2_seo/data/stats_history.json` | **Create** (empty `[]`) — weekly snapshots |
| `business2_seo/config.py` | **Edit** — add `CF_API_TOKEN`, `CF_ACCOUNT_ID`, `STATS_HISTORY_FILE` |
| `business2_seo/orchestrator.py` | **Edit** — wire StatsAgent after publish step |
| `business1_newsletter/agents/monitor_agent.py` | **Edit** — replace send_weekly_digest with HTML version, pull broadcast stats |
| `business1_newsletter/data/affiliate_links.json` | **Edit** — add UTM params |
| `business2_seo/data/affiliate_links.json` | **Edit** — add UTM params |

---

## Acceptance Criteria

- [ ] Every Sunday: HTML report email arrives at `ALERT_EMAIL` with newsletter + site stats
- [ ] Subscriber count shows week-over-week delta (e.g. "+12 this week")
- [ ] Latest broadcast open rate and click rate included
- [ ] Cloudflare weekly page views included
- [ ] Top 3 articles by traffic listed
- [ ] `stats_history.json` grows by one entry each Sunday
- [ ] All affiliate URLs include UTM parameters
- [ ] No new GitHub Secrets required (uses existing `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`)

---

## Technical Notes

### Cloudflare Analytics GraphQL

Uses the existing `CLOUDFLARE_API_TOKEN` secret. Query targets the Pages project analytics:

```python
# Endpoint
CLOUDFLARE_GRAPHQL = "https://api.cloudflare.com/client/v4/graphql"

# Query: weekly page views by path
query = """
{
  viewer {
    accounts(filter: { accountTag: $accountId }) {
      pagesAnalytics(
        filter: { datetime_geq: $start, datetime_leq: $end, projectName: "sutraflow-seo" }
        limit: 10
        orderBy: [sum_pageViews_DESC]
      ) {
        dimensions { path }
        sum { pageViews }
      }
    }
  }
}
"""
```

Note: If Pages analytics API doesn't expose path-level data, fall back to total page views only. The report still delivers value with newsletter stats + totals.

### Kit Broadcast Stats

Already implemented in `publisher_agent.py:get_broadcast_stats()` — just needs to be called with the last broadcast ID from `state.json`:

```python
# business1_newsletter/data/state.json
"last_broadcast_id": "23320855"  # already stored
```

---

## Dependencies & Risks

| Risk | Mitigation |
|---|---|
| Cloudflare Pages analytics API may not support path-level breakdown | Fall back to total views; still useful |
| Kit API rate limits on stats fetches | Stats fetched once per week — well within free tier limits |
| UTM params break affiliate tracking on Impact/PartnerStack | Test: Impact ignores query params after the ref ID; PartnerStack passes them through. Low risk |
| Notion affiliate URL doesn't support query params cleanly | Append `?utm_source=sutraflow` — Notion's affiliate system passes these through |

---

## Sources & References

- Existing broadcast stats method: `business1_newsletter/agents/publisher_agent.py:127`
- Existing weekly digest: `business1_newsletter/agents/monitor_agent.py:155`
- Current state tracking: `business2_seo/data/state.json`
- Cloudflare GraphQL API: https://developers.cloudflare.com/analytics/graphql-api/
- Kit broadcast stats API: https://developers.kit.com/v3#broadcast-stats
