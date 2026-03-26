# Zero-Touch Business Portfolio — Project Status

**Last updated:** 2026-03-25
**Owner:** Samik (sutrafloworg on GitHub)
**Site:** https://sutraflow.org
**Repo:** GitHub (private) — sutrafloworg

---

## What This Project Is

An autonomous passive-income portfolio of AI-powered businesses that run on GitHub Actions + Claude API with near-zero human intervention. The goal is $2K–$10K/month passive revenue at <$50/month infrastructure cost.

---

## Current Businesses (3 Live)

### Business 1: AI Tools Weekly Newsletter (`business1_newsletter/`)
- **What:** Automated newsletter curating AI tool news + affiliate links
- **Platform:** Kit (ConvertKit) — free tier (up to 10K subscribers)
- **Schedule:** Every Tuesday 7am UTC via GitHub Actions
- **Revenue model:** Affiliate commissions in email CTAs
- **Agent flow:** Feed Agent → Content Agent (Claude Haiku) → Publisher Agent (Kit API) → Monitor Agent
- **Status:** LIVE and running

### Business 2: Programmatic SEO Affiliate Site (`business2_seo/`)
- **What:** AI tool comparison/review articles auto-generated and published
- **Platform:** Hugo static site on Cloudflare Pages (free) at sutraflow.org
- **Schedule:** Every Sunday + Wednesday 2am UTC, 8 articles per run
- **Revenue model:** Affiliate commissions on AI tool links in articles
- **Agent flow:** Keyword Agent → Content Agent (Claude Haiku) → Publisher Agent (git commit → Cloudflare auto-deploys) → Monitor Agent
- **Content:** ~9 published articles, ~42 keywords pending, 50+ seed keywords in `keywords.csv`
- **SEO:** IndexNow auto-submits new URLs to Bing/Yandex on every deploy
- **Products page:** /products/ exists with 4 digital product listings (awaiting Gumroad setup)
- **Status:** LIVE and running

### Business 3: LocalRank Sentinel (`business3_local_seo/`)
- **What:** Weekly local SEO ranking scans for businesses across 12 US cities
- **Platform:** SerpAPI (250 free searches/month)
- **Schedule:** Every Monday 3am UTC via GitHub Actions
- **Coverage:** 12 cities x 5 categories = 60 searches/week (240/month within free limit)
- **Revenue model:** Sell SEO audit reports ($10) + ongoing monitoring subscriptions ($5/month)
- **Agent flow:** Scanner Agent (SerpAPI) → Analyzer Agent → Report Agent (Claude + PDF) → Outreach Agent (Gmail)
- **Status:** LIVE — scanning, but Stripe payment links not yet set up

---

## Tech Stack

| Component | Service | Cost |
|-----------|---------|------|
| Content generation | Claude Haiku 4.5 API | ~$3–8/month |
| Scheduling | GitHub Actions (free tier) | $0 |
| Newsletter | Kit/ConvertKit (free <10K subs) | $0 |
| Site hosting | Cloudflare Pages | $0 |
| Site framework | Hugo + PaperMod theme | $0 |
| Local SEO data | SerpAPI (250/month free) | $0 |
| Error alerts | Gmail SMTP | $0 |
| Domain | sutraflow.org | ~$12/year |
| **Total** | | **~$5–10/month** |

---

## Affiliate Program Status

| Program | Status | Commission | Affiliate ID |
|---------|--------|------------|--------------|
| **Rytr** | ACTIVE | 30% recurring 12mo | `?via=sutraflow` |
| Writesonic | NOT YET APPLIED | 30% recurring | YOUR_REF_ID |
| Surfer SEO | NOT YET APPLIED (wait for 25+ articles) | 25% recurring | YOUR_REF_ID |
| Notion | PAUSED by Notion | $50/signup + 20% yr1 | YOUR_REF_ID |
| ~~Copy.ai~~ | DEPRECATED March 2026 | — | — |
| ~~Jasper AI~~ | PERMANENTLY DEAD Jan 2025 | — | — |
| ~~Semrush~~ | DECLINED (low reach) | — | — |
| ~~GetResponse~~ | DECLINED (audience alignment) | — | — |

---

## What's Automated (Zero-Touch)

- Content generates 2x/week (Sun + Wed), 8 articles per run
- IndexNow auto-submits new URLs to Bing/Yandex after each deploy
- Newsletter sends every Tuesday at 7am UTC
- LocalRank scans 12 cities weekly on Monday
- Weekly stats report emails every Sunday
- Self-correction: API failures retry with exponential backoff, orphaned keywords auto-reset, email alerts on 3+ consecutive failures

---

## Remaining Manual Actions (One-Time Setup)

### HIGH PRIORITY (revenue-blocking):
1. **Apply to Writesonic affiliate** → https://writesonic.com/affiliates (30% recurring)
2. **Replace YOUR_REF_ID** in published articles for Writesonic/Surfer/Notion after approval
3. **Set up Stripe** → create payment links: "$10 SEO Audit" + "$5/month Map Pack Guardian" → add STRIPE_PAYMENT_URL to GitHub Secrets
4. **Run content pipeline** → GitHub → Actions → "SEO Site — Content Generation" → Run workflow (generates Rytr review + comparison articles)

### MEDIUM PRIORITY:
5. **Google Search Console** → verify sutraflow.org + submit sitemap.xml
6. **Kit Recommendations** → Kit → Settings → Recommendations → Enable
7. **Create Gumroad account** → https://gumroad.com (for selling digital products on /products/ page)
8. **Apply to Surfer SEO affiliate** after reaching 25+ published articles

### LOW PRIORITY:
9. Submit sitemap to Bing Webmaster Tools
10. Post landing page URL in Reddit (r/SideProject, r/Entrepreneur) once

---

## Expansion Roadmap (from plan document)

Full plan: `docs/plans/2026-03-24-001-feat-autonomous-business-portfolio-expansion-plan.md`

| Phase | What | When | Status |
|-------|------|------|--------|
| **Phase 0** | Revenue Activation — fix affiliate links, products page, content velocity | Week 1 | DONE (automated parts) |
| **Phase 1** | Content Acceleration — E-E-A-T hardening, content quality scoring | Weeks 2–3 | NOT STARTED |
| **Phase 2** | Digital Products — create ebooks/templates, Gumroad storefront | Weeks 3–5 | NOT STARTED |
| **Phase 3** | VPS + n8n — self-hosted orchestration on Hetzner CX22 ($6/mo) | Weeks 5–8 | NOT STARTED |
| **Phase 4** | Vertical Micro-SaaS — highest-ceiling revenue model | Weeks 8–14 | NOT STARTED |
| **Phase 5** | Advanced Models — API-as-a-Service, lead gen, industry reports | Weeks 14+ | NOT STARTED |

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `MASTER_SETUP.md` | Complete setup checklist for new deployments |
| `PROJECT_STATUS.md` | This file — full project status for any LLM to read |
| `info.txt` | Quick reference with credentials and action items |
| `business1_newsletter/data/affiliate_links.json` | Newsletter affiliate config |
| `business2_seo/data/affiliate_links.json` | SEO site affiliate config |
| `business2_seo/data/keywords.csv` | SEO keyword pipeline (pending/done status) |
| `business2_seo/hugo_site/config.toml` | Hugo site configuration |
| `business3_local_seo/data/cities.json` | LocalRank city coverage |
| `.github/workflows/seo_weekly.yml` | SEO content generation workflow |
| `.github/workflows/newsletter_weekly.yml` | Newsletter automation workflow |
| `docs/plans/*.md` | Expansion plans and feature specs |

---

## Revenue Targets (Realistic)

| Timeline | Newsletter | SEO Site | LocalRank | Total |
|----------|-----------|----------|-----------|-------|
| Month 1 | $0 | $0 | $0 | $0 |
| Month 3 | $50–200 | $0–50 | $0–100 | $50–350 |
| Month 6 | $400–1,000 | $500–2,500 | $200–500 | $1,100–4,000 |
| Month 12 | $1,000–4,000 | $3,000–15,000 | $500–2,000 | $4,500–21,000 |
