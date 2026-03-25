# Zero-Touch Business System — Master Setup Guide

## Market Thesis

**Business 1 (Newsletter):** AI tool discovery is the #1 growth pain for professionals in 2026 —
SaaS affiliate programs pay 25–40% recurring commissions, meaning a single subscriber referral
compounds for 12+ months with zero additional work.

**Business 2 (SEO Site):** Long-tail AI tool comparison keywords ("Claude vs GPT-4 for lawyers")
have high commercial intent, thin competition, and perfectly match programmatic content generation —
Google sends free traffic forever once pages are indexed.

---

## Architecture Overview

```
GitHub Actions (free scheduler)
       │
       ├── Business 1: EVERY TUESDAY 7am UTC
       │      Feed Agent → Content Agent (Claude Haiku) → Publisher Agent (Kit API) → Monitor Agent
       │
       ├── Business 2: EVERY SUNDAY + WEDNESDAY 2am UTC
       │      Keyword Agent → Content Agent (Claude Haiku) → Publisher Agent (→ Git commit) → Monitor Agent
       │                                                                  └── Cloudflare Pages auto-deploys Hugo site
       │
       └── Business 3: EVERY MONDAY 3am UTC (LocalRank Sentinel)
              Scanner Agent (SerpAPI) → Analyzer Agent → Report Agent (Claude + PDF) → Outreach Agent (Gmail)
              12 cities × 5 categories = 60 searches/week (240/month within 250 free limit)
```

**Monthly operating cost:**
- Claude API: ~$3–8/month total for both businesses
- Hosting: $0 (Cloudflare Pages free)
- Email platform: $0 (Kit free up to 10K subscribers)
- Domain: ~$1/month (~$12/year, one-time)
- **Total: ~$5–10/month**

---

## EXECUTION MAP — The Only Things You Need to Click

### Phase 1: Setup (ONE TIME — ~2 hours)

#### Step 1: Create accounts (15 min)

- [ ] **GitHub** — https://github.com/signup (free)
- [ ] **Cloudflare** — https://dash.cloudflare.com/sign-up (free)
- [ ] **Kit (ConvertKit)** — https://app.kit.com/users/signup (free up to 10K subs)
- [ ] **Google Gmail** — you likely have this already
- [ ] **Domain registrar** — Cloudflare Registrar (~$10/year, cheapest option)
  - Buy a domain like `aitoolsinsider.com` or `aiweekly.pro`

#### Step 2: Set up the GitHub repository (10 min)

- [ ] Create a NEW GitHub repo (public or private, doesn't matter)
  - Name suggestion: `zero-touch-business`
- [ ] Upload all files from this folder to the repo root
  - (You can drag-and-drop files in GitHub UI, or use Git)
- [ ] The repo structure should look like:
  ```
  /business1_newsletter/
  /business2_seo/
  ```
- [ ] IMPORTANT: Move both `.github/workflows/` folders to the ROOT `.github/workflows/`:
  ```
  /.github/workflows/newsletter_weekly.yml
  /.github/workflows/seo_weekly.yml
  ```

#### Step 3: Add GitHub Secrets (10 min)

Go to: Your Repo → Settings → Secrets and Variables → Actions → New Repository Secret

Add these secrets:

| Secret Name | Where to get it | Required for |
|---|---|---|
| `ANTHROPIC_API_KEY` | https://console.anthropic.com/account/keys | Both businesses |
| `KIT_API_SECRET` | Kit Dashboard → Settings → Developer → API Secret | Business 1 only |
| `KIT_API_KEY` | Kit Dashboard → Settings → Developer → API Key | Business 1 only |
| `KIT_FORM_ID` | Kit → Landing Pages → your form URL (the number at the end) | Business 1 only |
| `SITE_DOMAIN` | Your domain e.g. `aitoolsinsider.com` | Business 2 only |
| `ALERT_EMAIL` | Your email address | Both (for error alerts) |
| `GMAIL_USER` | Your Gmail address | Both (for sending alerts) |
| `GMAIL_APP_PASSWORD` | Gmail → Account → Security → 2-Step Verification → App passwords | Both |

**Gmail App Password steps:**
1. Go to myaccount.google.com/security
2. Enable 2-Step Verification if not already on
3. Search "App passwords" → Create one for "Mail"
4. Copy the 16-character password → paste as `GMAIL_APP_PASSWORD`

#### Step 4: Configure Cloudflare Pages (10 min)

- [ ] Log into Cloudflare Dashboard → Pages → Create a project
- [ ] Connect to GitHub → Select your repo
- [ ] Build settings:
  - **Framework preset:** Hugo
  - **Build command:** `cd business2_seo/hugo_site && hugo`
  - **Build output directory:** `business2_seo/hugo_site/public`
- [ ] Click Save and Deploy (first deploy will be blank — that's OK)
- [ ] After deploy: Pages → your project → Custom Domains → Add your domain

#### Step 5: Install Hugo theme (5 min)

In your local terminal (or GitHub Codespace):
```bash
cd business2_seo/hugo_site
git submodule add https://github.com/adityatelange/hugo-PaperMod themes/PaperMod
git add .gitmodules themes/
git commit -m "add PaperMod theme"
git push
```

#### Step 6: Update config files (5 min)

- [ ] In `business2_seo/hugo_site/config.toml` — change `baseURL` to your actual domain
- [ ] In `business2_seo/hugo_site/static/robots.txt` — change domain
- [ ] In `business1_newsletter/data/affiliate_links.json` — fill in `YOUR_REF_ID` with actual affiliate IDs (see Step 7)

#### Step 7: Sign up for affiliate programs (30 min)

Apply to these programs — most approve instantly or within 24 hours:

| Program | Apply at | Commission | Status |
|---|---|---|---|
| Copy.ai | https://www.copy.ai/partners | 45% recurring | Apply first — no traffic minimum |
| Rytr | https://rytr.me/affiliate | 30% recurring 12mo | Easy approval, beginner-friendly |
| Writesonic | https://writesonic.com/affiliate | 30% recurring | Moderate — needs platform review |
| Surfer SEO | https://surferseo.com/affiliate/ | 25% recurring | Harder — apply after 25+ articles |
| Notion | https://www.notion.so/affiliates | $50 signup + 20% yr1 | Currently paused — check periodically |

**Note:** Semrush declined (low reach), GetResponse declined (audience alignment), Jasper AI program permanently shut down (Jan 2025). The programs above are verified active and accessible for new publishers.

- [ ] After approval, replace `YOUR_REF_ID` in `business1_newsletter/data/affiliate_links.json`
- [ ] Replace `YOUR_REF_ID` in `business2_seo/data/affiliate_links.json`
- [ ] Commit and push the changes

#### Step 8: Enable GitHub Actions (2 min)

- [ ] Go to your repo → Actions tab → Click "I understand my workflows, go ahead and enable them"
- [ ] The workflows will now run automatically on schedule

#### Step 9: Test run (optional but recommended, 5 min)

- [ ] Go to Actions → "Newsletter Weekly Automation" → Run workflow (manual trigger)
- [ ] Watch the logs — should succeed in ~2 minutes
- [ ] Go to Actions → "SEO Site — Weekly Content Generation" → Run workflow
- [ ] Check that new .md files appear in `business2_seo/hugo_site/content/posts/`

#### Step 10: Set up Kit subscriber form (10 min)

- [ ] In Kit: Create a Landing Page with your newsletter signup form
- [ ] Share the landing page URL in your bio/social profiles
- [ ] Enable Kit's "Recommendations" feature (Settings → Recommendations)
  - This auto-grows your list via cross-promotion with other Kit newsletters — FREE

---

## That's it. The system is ON. ✓

From this point forward:
- **Every Tuesday at 7am UTC:** Newsletter generates and sends automatically
- **Every Sunday at 2am UTC:** 5 new SEO articles publish automatically
- **Errors:** You get an email alert with exact instructions

---

## Failure Protocols

### If Claude API fails
- **Auto-retry:** 3 attempts with exponential backoff (5s, 10s, 20s)
- **If all retries fail:** Pipeline moves to next item, logs error
- **If 3 consecutive runs fail:** Email alert sent to you with diagnosis
- **Your action:** Check https://console.anthropic.com — add credits if depleted

### If Kit API fails (newsletter not sent)
- **Auto-save:** Full newsletter HTML saved to `business1_newsletter/logs/unsent_newsletter_TIMESTAMP.html`
- **Email alert:** Sent immediately with the fallback file path
- **Auto-retry:** Next Tuesday's run will attempt to re-send queued newsletters
- **Your action:** Check Kit dashboard for API status, verify `KIT_API_SECRET` is valid

### If Cloudflare Pages build fails
- **Detection:** Monitor Agent HTTP-checks your domain after each commit
- **Email alert:** Sent with "Site Unreachable" warning
- **Your action:** Check Cloudflare Pages → your project → Deployments for build logs

### If a customer/reader replies with a complaint
- **Newsletter:** They reply to your Kit From email — Kit forwards it to your Gmail
- **Response:** Reply normally — Kit handles unsubscribes automatically
- **SEO site:** No "customers" — readers just visit and click. No support needed.

### If Google deindexes pages
- **Detection:** Check Google Search Console (set up once, monitors forever)
- **Response:** Review content quality, ensure affiliate disclosure is visible
- **Prevention:** Affiliate disclosure is auto-inserted in every article's frontmatter

---

## Revenue Timeline (Realistic)

### Business 1 — Newsletter
| Month | Subscribers | Revenue Source | Estimated Revenue |
|-------|------------|----------------|-------------------|
| 1 | 0–50 | None | $0 |
| 2 | 50–150 | First affiliate clicks | $10–50 |
| 3 | 150–400 | Affiliate commissions | $50–200 |
| 4 | 400–800 | Affiliate + launch paid tier | $150–500 |
| 6 | 800–2,000 | Affiliate + paid subs | $400–1,000 |
| 12 | 2,000–8,000 | Affiliate + paid + sponsors | $1,000–4,000 |

### Business 2 — SEO Site
| Month | Articles | Monthly Traffic | Revenue |
|-------|---------|----------------|---------|
| 1–2 | 40 | 0 | $0 |
| 3 | 80 | 100–500 | $0–50 |
| 4 | 100 | 500–2,000 | $50–300 |
| 5 | 120 | 2,000–8,000 | $200–800 |
| 6 | 140 | 8,000–25,000 | $500–2,500 |
| 12 | 300+ | 50,000+ | $3,000–15,000 |

---

## Growth Accelerators (Optional — No Automation Required)

These are one-time setup actions that compound passively:

1. **Newsletter:** Submit to Substack Recommendations network (Kit has equivalent)
2. **Newsletter:** Post your landing page URL in Reddit communities once (r/SideProject, r/Entrepreneur)
3. **SEO Site:** Submit sitemap to Google Search Console once
4. **SEO Site:** Submit sitemap to Bing Webmaster Tools once
5. **Cross-promotion:** Link from newsletter to SEO site articles — builds backlinks organically

None of these require ongoing effort. Do them once in Week 1 and forget.
