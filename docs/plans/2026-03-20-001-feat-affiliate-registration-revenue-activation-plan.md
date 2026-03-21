---
title: Affiliate Registration & Revenue Activation
type: feat
status: active
date: 2026-03-20
---

# Affiliate Registration & Revenue Activation

## Overview

Both businesses are **fully operational and generating content automatically**, but are earning **$0 in affiliate revenue** because every affiliate link in both systems still contains the placeholder `YOUR_REF_ID`. This plan activates revenue by registering for 5 affiliate programs, collecting IDs, and updating two JSON files.

**The automation is done. This plan completes the revenue switch.**

---

## Current State Assessment

### ✅ What's Already Working

| Component | Status | Evidence |
|---|---|---|
| GitHub repo | Live (sutrafloworg) | Commits running on schedule |
| GitHub Actions | Active | Both workflows executing |
| Newsletter (Business 1) | Running | 2 newsletters sent, last run 2026-03-17 |
| SEO site (Business 2) | Running | 4 articles published, last run 2026-03-16 |
| Kit/ConvertKit | Connected | `last_broadcast_id: 23320855` |
| Cloudflare Pages | Deployed | Site live at `sutraflow.org` |
| Hugo theme (PaperMod) | Installed | `business2_seo/hugo_site/themes/PaperMod/` exists |
| Domain config | Set | `baseURL = "https://sutraflow.org/"` in `config.toml` |
| robots.txt | Updated | Points to `sutraflow.org/sitemap.xml` |

### ❌ What's Blocking Revenue (The Only Gap)

```
business1_newsletter/data/affiliate_links.json  →  ALL 5 tools have YOUR_REF_ID
business2_seo/data/affiliate_links.json         →  ALL 5 tools have YOUR_REF_ID
```

Every affiliate CTA in every newsletter and every article is a **broken link** until these are replaced.

---

## Affiliate Programs to Register

Prepare these details before signing up (same for all programs):
- **Website URL:** `https://sutraflow.org`
- **Niche:** AI tools, productivity, SaaS reviews
- **Content type:** Newsletter + SEO blog
- **Traffic:** Honest — just starting, growing (they care about niche fit more than traffic at this stage)
- **Payment method:** PayPal (fastest to set up) or bank transfer

### Program 1: Semrush

| Field | Value |
|---|---|
| **Apply at** | https://www.semrush.com/partner/affiliates/ |
| **Commission** | $10/free trial + $50–$450/paid sale |
| **Cookie** | 120 days |
| **Approval** | Usually 24–48h, manual review |
| **Affiliate URL format** | `https://semrush.sjv.io/YOUR_REF_ID` |
| **Network** | Impact (requires Impact account) |

**Steps:**
1. Go to https://www.semrush.com/partner/affiliates/
2. Click "Join the program"
3. You'll be redirected to Impact — create a free Impact account if needed
4. Fill in: website URL, content description, monthly traffic (estimate ~500/month to start)
5. After approval, go to Impact → Semrush campaign → get your unique tracking link
6. The ID is the alphanumeric part at the end of the tracking link

---

### Program 2: Surfer SEO

| Field | Value |
|---|---|
| **Apply at** | https://surferseo.com/affiliate/ |
| **Commission** | 25% recurring |
| **Cookie** | 60 days |
| **Approval** | Usually instant or same day |
| **Affiliate URL format** | `https://surferseo.com/?via=YOUR_REF_ID` |
| **Network** | PartnerStack |

**Steps:**
1. Go to https://surferseo.com/affiliate/
2. Click "Apply now" — redirects to PartnerStack
3. Create PartnerStack account with your email
4. After approval, go to PartnerStack dashboard → your unique referral link
5. The ID is the `?via=XXXXXXX` parameter value

---

### Program 3: Writesonic

| Field | Value |
|---|---|
| **Apply at** | https://writesonic.com/affiliate |
| **Commission** | 30% recurring |
| **Cookie** | 60 days |
| **Approval** | Usually instant |
| **Affiliate URL format** | `https://writesonic.com/?via=YOUR_REF_ID` |
| **Network** | PartnerStack |

**Steps:**
1. Go to https://writesonic.com/affiliate
2. Click "Become an affiliate" — redirects to PartnerStack
3. Fill in your website details
4. After approval, get your referral link from PartnerStack
5. The ID is the `?via=XXXXXXX` parameter value

---

### Program 4: Notion

| Field | Value |
|---|---|
| **Apply at** | https://www.notion.so/affiliates |
| **Commission** | $50/new paid signup + 20% revenue for year 1 |
| **Cookie** | 180 days |
| **Approval** | Manual, 3–7 business days |
| **Affiliate URL format** | `https://affiliate.notion.so/YOUR_REF_ID` |
| **Network** | Direct (Notion's own program) |

**Steps:**
1. Go to https://www.notion.so/affiliates
2. Click "Apply" — fill out the application form
3. Describe your newsletter + SEO site targeting productivity professionals
4. After approval, access your unique affiliate dashboard link
5. The ID is the final path segment in your affiliate URL

---

### Program 5: GetResponse

| Field | Value |
|---|---|
| **Apply at** | https://www.getresponse.com/affiliate-program |
| **Commission** | 40% recurring for 12 months |
| **Cookie** | 90 days |
| **Approval** | Usually instant or same day |
| **Affiliate URL format** | `https://www.getresponse.com/referral/YOUR_REF_ID` |
| **Network** | Direct (GetResponse's own program) |

**Steps:**
1. Go to https://www.getresponse.com/affiliate-program
2. Click "Join now"
3. Create a free affiliate account
4. Go to dashboard → Affiliate links → your referral link
5. The ID is the alphanumeric string in the referral URL

---

## Payment Information Setup

For each affiliate program, after account creation:

1. **Impact (Semrush):** Account → Settings → Payment → Add PayPal or bank wire
2. **PartnerStack (Surfer SEO, Writesonic):** Settings → Payout → Add PayPal or Stripe
3. **Notion direct:** Dashboard → Payout settings → PayPal
4. **GetResponse direct:** Dashboard → Billing & payments → Add PayPal

**Minimum payout thresholds:**
- Semrush (Impact): $100
- Surfer SEO (PartnerStack): $25
- Writesonic (PartnerStack): $25
- Notion: $50
- GetResponse: $50

---

## Updating the JSON Files

Once you have all 5 affiliate IDs, update **both** files:

### File 1: `business1_newsletter/data/affiliate_links.json`

Replace each `YOUR_REF_ID` with the actual affiliate ID:

```json
"semrush": {
  "affiliate_url": "https://semrush.sjv.io/SEMRUSH_ACTUAL_ID"
},
"notion": {
  "affiliate_url": "https://affiliate.notion.so/NOTION_ACTUAL_ID"
},
"writesonic": {
  "affiliate_url": "https://writesonic.com/?via=WRITESONIC_ACTUAL_ID"
},
"getresponse": {
  "affiliate_url": "https://www.getresponse.com/referral/GETRESPONSE_ACTUAL_ID"
},
"surfer_seo": {
  "affiliate_url": "https://surferseo.com/?via=SURFERSEO_ACTUAL_ID"
}
```

### File 2: `business2_seo/data/affiliate_links.json`

Same replacements — update both `affiliate_url` AND the `cta_button` HTML attribute:

```json
"semrush": {
  "affiliate_url": "https://semrush.sjv.io/SEMRUSH_ACTUAL_ID",
  "cta_button": "<a href='https://semrush.sjv.io/SEMRUSH_ACTUAL_ID' class='btn-affiliate' rel='nofollow sponsored'>Try Semrush Free →</a>"
},
...
```

### Commit the changes

```bash
cd /mnt/c/Users/samik/Documents/claude_busines
git add business1_newsletter/data/affiliate_links.json business2_seo/data/affiliate_links.json
git commit -m "revenue: activate affiliate links with real IDs"
git push
```

The system will automatically pick up the new IDs in the next scheduled run (Tuesday for newsletter, Sunday for SEO).

---

## Acceptance Criteria

- [ ] Semrush affiliate account created, ID obtained, payment info added
- [ ] Surfer SEO affiliate account created, ID obtained, payment info added
- [ ] Writesonic affiliate account created, ID obtained, payment info added
- [ ] Notion affiliate application submitted (may take 3–7 days for approval)
- [ ] GetResponse affiliate account created, ID obtained, payment info added
- [ ] `business1_newsletter/data/affiliate_links.json` — zero `YOUR_REF_ID` remaining
- [ ] `business2_seo/data/affiliate_links.json` — zero `YOUR_REF_ID` remaining (including inside `cta_button` HTML)
- [ ] Changes committed and pushed to GitHub
- [ ] Verified: next newsletter run contains real clickable affiliate links
- [ ] Verified: SEO articles on `sutraflow.org` contain real affiliate buttons

---

## Dependencies & Risks

| Risk | Mitigation |
|---|---|
| Notion approval takes 3–7 days | Apply first; update the other 4 immediately |
| Semrush may require traffic proof | Be honest — describe newsletter + SEO niche, they care more about fit |
| PartnerStack account merge (Surfer + Writesonic) | Both live in the same PartnerStack account — easier, not harder |
| Links are broken in already-published content | After updating JSON, old published articles still have placeholder text baked in. Republish or manually patch those 4 existing articles |

### Already-Published Content Fix

The 4 already-published SEO articles and 2 newsletters used `YOUR_REF_ID` as placeholder. After updating the JSON, newly generated content will be correct. For existing articles:

```bash
# Find files with placeholder
grep -r "YOUR_REF_ID" business2_seo/hugo_site/content/
```

Replace manually in each file, or trigger a re-run that regenerates them.

---

## Sources & References

- Semrush affiliate program: https://www.semrush.com/partner/affiliates/
- Surfer SEO affiliate: https://surferseo.com/affiliate/
- Writesonic affiliate: https://writesonic.com/affiliate
- Notion affiliates: https://www.notion.so/affiliates
- GetResponse affiliate: https://www.getresponse.com/affiliate-program
- Business 1 affiliate config: `business1_newsletter/data/affiliate_links.json`
- Business 2 affiliate config: `business2_seo/data/affiliate_links.json`
- Master setup guide: `MASTER_SETUP.md` (Step 7)
