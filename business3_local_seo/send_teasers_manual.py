import json
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone
import config
from agents.outreach_agent import OutreachAgent
from agents.fulfillment_agent import FulfillmentAgent

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger("manual_outreach")

def get_alerts_from_history():
    rankings_file = Path("data/rankings_history.json")
    with open(rankings_file) as f:
        history = json.load(f)
        
    alerts = []
    now = datetime.now(timezone.utc).isoformat()
    
    for key, data in history.items():
        if "snapshots" not in data or len(data["snapshots"]) < 2:
            continue
            
        snapshots = data["snapshots"]
        prev_results = snapshots[-2]["results"]
        curr_results = snapshots[-1]["results"]
        
        prev_lookup = {}
        for biz in prev_results:
            pid = biz.get("place_id") or biz.get("name", "")
            prev_lookup[pid] = biz
            
        for biz in curr_results:
            pid = biz.get("place_id") or biz.get("name", "")
            prev = prev_lookup.get(pid)
            
            if not prev:
                continue
                
            prev_rank = prev.get("rank", 99)
            curr_rank = biz.get("rank", 99)
            
            if curr_rank > prev_rank:
                alerts.append({
                    "category_key": key,
                    "business_name": biz["name"],
                    "address": biz.get("address", ""),
                    "phone": biz.get("phone", ""),
                    "website": biz.get("website", ""),
                    "prev_rank": prev_rank,
                    "curr_rank": curr_rank,
                    "rank_change": curr_rank - prev_rank,
                    "rating": biz.get("rating", 0),
                    "reviews": biz.get("reviews", 0),
                    "prev_reviews": prev.get("reviews", 0),
                    "reasons": ["Rank dropped manually extracted"],
                    "scan_date": now,
                    "weeks_tracked": len(snapshots),
                    "insights": {},
                })
    return alerts

def main():
    alerts = get_alerts_from_history()
    logger.info(f"Reconstructed {len(alerts)} rank drop alerts from history")
    
    if not alerts:
        return
        
    outreach = OutreachAgent(
        gmail_user=config.GMAIL_USER,
        gmail_app_password=config.GMAIL_APP_PASSWORD,
        payment_url=config.PAYMENT_URL_MONITORING,
        payment_url_audit=config.PAYMENT_URL_AUDIT,
    )
    
    # Send the teasers! We've already removed the caps.
    summary = outreach.process_batch_teasers(alerts)
    logger.info(f"Outreach Complete: {summary}")
    
    contacted = summary.get("contacted", [])
    if contacted:
        logger.info(f"Registering {len(contacted)} alerts to pending_reports.json...")
        fulfillment = FulfillmentAgent(
            index_file=config.PENDING_REPORTS_FILE,
            outreach=outreach,
        )
        fulfillment.register_alerts(contacted)

if __name__ == '__main__':
    main()
