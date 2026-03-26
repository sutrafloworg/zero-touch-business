"""
Fulfillment Agent — delivers PDF audit reports after Stripe payment.

Manages the pending_reports index:
  - Orchestrator writes entries after generating PDFs and sending teaser emails
  - Webhook server calls deliver() after payment confirmation
  - Entries are marked as 'delivered' after successful email

Index format (pending_reports.json):
{
  "reports": [
    {
      "id": "abc123",
      "email": "owner@business.com",
      "business_name": "Ace Plumbing",
      "pdf_path": "/path/to/audit.pdf",
      "category_key": "losangeles_ca_plumber",
      "created_at": "2026-03-26T...",
      "status": "pending|delivered|failed",
      "delivered_at": null
    }
  ]
}
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agents.outreach_agent import OutreachAgent

logger = logging.getLogger(__name__)


class FulfillmentAgent:
    def __init__(self, index_file: Path, outreach: OutreachAgent):
        self.index_file = index_file
        self.outreach = outreach

    def _load_index(self) -> dict:
        try:
            with open(self.index_file) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"reports": []}

    def _save_index(self, index: dict) -> None:
        with open(self.index_file, "w") as f:
            json.dump(index, f, indent=2, default=str)

    def register_reports(self, contacted: list[dict]) -> list[str]:
        """Add newly generated reports to the index. Returns list of report IDs."""
        index = self._load_index()
        report_ids = []

        for entry in contacted:
            report_id = uuid.uuid4().hex[:12]
            index["reports"].append({
                "id": report_id,
                "email": entry["email"],
                "business_name": entry["business_name"],
                "pdf_path": entry["pdf_path"],
                "category_key": entry.get("category_key", ""),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "pending",
                "delivered_at": None,
            })
            report_ids.append(report_id)

        self._save_index(index)
        logger.info(f"Fulfillment: registered {len(report_ids)} reports in index")
        return report_ids

    def deliver(self, customer_email: str) -> dict:
        """Find pending report for this email and deliver the PDF.

        Called by the webhook server after Stripe payment confirmation.
        Returns {"success": bool, "business_name": str, "report_id": str}.
        """
        index = self._load_index()
        email_lower = customer_email.lower().strip()

        # Find the most recent pending report for this email
        matching = [
            r for r in index["reports"]
            if r["email"].lower().strip() == email_lower and r["status"] == "pending"
        ]

        if not matching:
            logger.warning(f"Fulfillment: no pending report for {customer_email}")
            return {"success": False, "error": "no_pending_report"}

        report = matching[-1]  # most recent
        pdf_path = Path(report["pdf_path"])

        if not pdf_path.exists():
            logger.error(f"Fulfillment: PDF not found at {pdf_path}")
            report["status"] = "failed"
            self._save_index(index)
            return {"success": False, "error": "pdf_not_found"}

        success = self.outreach.send_fulfillment_email(
            to_email=report["email"],
            business_name=report["business_name"],
            pdf_path=pdf_path,
        )

        if success:
            report["status"] = "delivered"
            report["delivered_at"] = datetime.now(timezone.utc).isoformat()
            logger.info(f"Fulfillment: delivered report {report['id']} to {report['email']}")
        else:
            report["status"] = "failed"
            logger.error(f"Fulfillment: delivery failed for {report['id']}")

        self._save_index(index)
        return {
            "success": success,
            "business_name": report["business_name"],
            "report_id": report["id"],
        }

    def get_stats(self) -> dict:
        """Return summary stats of the report index."""
        index = self._load_index()
        reports = index.get("reports", [])
        return {
            "total": len(reports),
            "pending": sum(1 for r in reports if r["status"] == "pending"),
            "delivered": sum(1 for r in reports if r["status"] == "delivered"),
            "failed": sum(1 for r in reports if r["status"] == "failed"),
        }
