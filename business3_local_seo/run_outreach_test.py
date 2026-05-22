import json
import logging
import sys
import time
from pathlib import Path
from agents.outreach_agent import OutreachAgent

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger("test_outreach")

def main():
    logger.info("Loading historical data to bypass SerpAPI scan...")
    rankings_file = Path("data/rankings_history.json")
    with open(rankings_file) as f:
        history = json.load(f)
    
    dates = sorted(history.keys())
    if not dates:
        logger.error("No dates found in history")
        return
        
    latest_scan = history[dates[-1]]
    
    candidates = []
    # Collect all unique candidate websites from the most recent scan
    for category_key, biz_list in latest_scan.items():
        if not isinstance(biz_list, list):
            continue
        for biz in biz_list:
            if not isinstance(biz, dict):
                continue
            url = biz.get('website', '')
            name = biz.get('name', 'Unknown')
            if url and url not in [c['url'] for c in candidates]:
                candidates.append({'url': url, 'name': name})
                
    logger.info(f"Found {len(candidates)} unique candidates with websites from {dates[-1]}")
    
    # Run Outreach
    outreach = OutreachAgent(
        gmail_user=None,  # Don't actually send emails right now, just scrape
        gmail_app_password=None,
        payment_url="https://buy.stripe.com/test",
        payment_url_audit="https://buy.stripe.com/test",
    )
    
    emails_found = []
    
    logger.info("Starting Web Scraping across all candidates...")
    start = time.time()
    
    # We will test the first 50 candidates to simulate the hit rate quickly,
    # as hitting 300+ full HTTP fetches takes several minutes.
    test_set = candidates[:50]
    
    for i, lead in enumerate(test_set):
        url = lead["url"]
        bz = lead["name"]
        try:
            email = outreach.find_email_from_website(url)
        except Exception as e:
            email = None
            
        if email:
            logger.info(f"[{i+1}/{len(test_set)}] MATCH: {bz} -> {email} ({url})")
            emails_found.append(email)
        else:
            logger.warning(f"[{i+1}/{len(test_set)}] MISS:  {bz} ({url})")
            
    logger.info(f"Finished testing {len(test_set)} websites in {time.time()-start:.1f}s")
    hit_rate = (len(emails_found) / max(1, len(test_set))) * 100
    
    logger.info(f"Total Emails Successfully Extracted: {len(emails_found)} out of {len(test_set)} ({hit_rate:.1f}%)")

if __name__ == '__main__':
    main()
