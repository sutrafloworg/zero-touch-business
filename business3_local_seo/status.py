"""
Search Sentinel — Status Dashboard

Run: python3 business3_local_seo/status.py

Shows:
  - Pipeline health (last run, failures, status)
  - Outreach metrics (emails sent, reports generated)
  - Pending reports awaiting payment
  - Search quota usage
  - Rankings coverage
"""
import json
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default if default is not None else {}


def main():
    state = load_json(DATA_DIR / "state.json", {})
    pending = load_json(DATA_DIR / "pending_reports.json", {"reports": []})
    usage = load_json(DATA_DIR / "search_usage.json", {})
    rankings = load_json(DATA_DIR / "rankings_history.json", {})
    contacts = load_json(DATA_DIR / "contacts.json", {})

    reports = pending.get("reports", [])
    pending_count = sum(1 for r in reports if r.get("status") == "pending")
    delivered_count = sum(1 for r in reports if r.get("status") == "delivered")
    failed_count = sum(1 for r in reports if r.get("status") == "failed")

    print("\n" + "=" * 60)
    print("  LOCALRANK SENTINEL — STATUS DASHBOARD")
    print("=" * 60)

    # Pipeline health
    last_run = state.get("last_run", "Never")
    if last_run != "Never":
        try:
            dt = datetime.fromisoformat(last_run)
            age = datetime.now(timezone.utc) - dt
            last_run_str = f"{dt.strftime('%Y-%m-%d %H:%M UTC')} ({age.days}d ago)"
        except Exception:
            last_run_str = last_run
    else:
        last_run_str = "Never"

    status = state.get("last_status", "Unknown")
    failures = state.get("consecutive_failures", 0)
    status_icon = "OK" if status == "OK" else "FAILED"

    print(f"\n  Pipeline Health")
    print(f"  {'─' * 40}")
    print(f"  Status:              {status_icon}")
    print(f"  Last run:            {last_run_str}")
    print(f"  Total runs:          {state.get('total_runs', 0)}")
    print(f"  Consecutive fails:   {failures} {'(CRITICAL!)' if failures >= 3 else ''}")
    if status != "OK":
        print(f"  Last error:          {status[:80]}")

    # Outreach metrics
    print(f"\n  Outreach Metrics (All Time)")
    print(f"  {'─' * 40}")
    print(f"  Emails sent:         {state.get('total_emails_sent', 0)}")
    print(f"  Reports generated:   {state.get('total_reports_generated', 0)}")
    print(f"  Contacts discovered: {len(contacts)}")

    # Last run details
    print(f"\n  Last Run Details")
    print(f"  {'─' * 40}")
    print(f"  Categories scanned:  {state.get('last_scans', 0)}")
    print(f"  Rank drops found:    {state.get('last_alerts', 0)}")
    print(f"  PDFs generated:      {state.get('last_reports', 0)}")
    print(f"  Teasers sent:        {state.get('last_emails_sent', 0)}")

    # Fulfillment / Payment tracking
    print(f"\n  Payment & Fulfillment")
    print(f"  {'─' * 40}")
    print(f"  Pending (awaiting $): {pending_count}")
    print(f"  Delivered (paid):     {delivered_count}")
    print(f"  Failed delivery:      {failed_count}")
    print(f"  Total reports:        {len(reports)}")

    if reports:
        print(f"\n  Recent Reports:")
        for r in sorted(reports, key=lambda x: x.get("created_at", ""), reverse=True)[:5]:
            status_label = r.get("status", "?").upper()
            biz = r.get("business_name", "Unknown")[:30]
            email = r.get("email", "no-email")
            created = r.get("created_at", "")[:10]
            print(f"    [{status_label:9}] {biz:<30} {email:<30} {created}")

    # Search quota
    print(f"\n  Search API Quota ({usage.get('month', 'unknown')})")
    print(f"  {'─' * 40}")
    serpapi_used = usage.get("serpapi", 0)
    valueserp_used = usage.get("valueserp", 0)
    print(f"  SerpAPI:     {serpapi_used}/245 used ({245 - serpapi_used} remaining)")
    print(f"  ValueSERP:   {valueserp_used}/95 used ({95 - valueserp_used} remaining)")

    # Rankings coverage
    print(f"\n  Rankings Coverage")
    print(f"  {'─' * 40}")
    print(f"  Categories tracked:  {len(rankings)}")
    categories_with_snapshots = sum(
        1 for v in rankings.values()
        if isinstance(v, dict) and len(v.get("snapshots", [])) >= 2
    )
    print(f"  With 2+ snapshots:   {categories_with_snapshots} (needed for drop detection)")

    # PDF reports on disk
    reports_dir = BASE_DIR / "reports"
    pdfs = list(reports_dir.glob("*.pdf")) if reports_dir.exists() else []
    print(f"  PDFs on disk:        {len(pdfs)}")

    print(f"\n{'=' * 60}")
    print(f"  Next scheduled run: Monday 1pm UTC (8am ET)")
    print(f"  Workflow: .github/workflows/local_seo_weekly.yml")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
