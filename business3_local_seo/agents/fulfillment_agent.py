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

    def deliver(self, customer_email: str, business_name: str = "",
                category_key: str = "") -> dict:
        """Find pending report for this email and deliver the PDF.

        Called by the webhook server after Stripe payment confirmation.
        If no pending report exists (e.g. customer paid before teaser was indexed),
        searches for the most recent PDF matching the business/category, or queues
        a delivery-pending record so the next pipeline run delivers it.

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
            # FALLBACK 1: Check for ANY report (including delivered) for this email
            # and re-deliver it — customer may have lost the email
            any_match = [
                r for r in index["reports"]
                if r["email"].lower().strip() == email_lower
            ]
            if any_match:
                report = any_match[-1]
                pdf_path = Path(report["pdf_path"])
                if pdf_path.exists():
                    logger.info(f"Fulfillment: re-delivering existing report to {customer_email}")
                    matching = [report]
                    report["status"] = "pending"  # reset to re-deliver

            if not matching:
                # FALLBACK 2: Search reports directory for any PDF matching
                # the business name or category key
                reports_dir = Path(__file__).parent.parent / "reports"
                found_pdf = None
                if business_name:
                    biz_slug = business_name.lower().replace(" ", "_").replace("'", "")
                    for pdf in sorted(reports_dir.glob("*.pdf"), reverse=True):
                        if biz_slug in pdf.name.lower():
                            found_pdf = pdf
                            break
                if not found_pdf and category_key:
                    for pdf in sorted(reports_dir.glob("*.pdf"), reverse=True):
                        if category_key.lower() in pdf.name.lower():
                            found_pdf = pdf
                            break

                if found_pdf:
                    # Create a new index entry and deliver
                    report_id = uuid.uuid4().hex[:12]
                    report = {
                        "id": report_id,
                        "email": customer_email,
                        "business_name": business_name or "Your Business",
                        "pdf_path": str(found_pdf),
                        "category_key": category_key,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "status": "pending",
                        "delivered_at": None,
                    }
                    index["reports"].append(report)
                    self._save_index(index)
                    matching = [report]
                    logger.info(f"Fulfillment: found PDF {found_pdf.name} via directory search for {customer_email}")

            if not matching:
                # FALLBACK 3: Queue for delivery — next pipeline run will pick it up
                report_id = uuid.uuid4().hex[:12]
                queued = {
                    "id": report_id,
                    "email": customer_email,
                    "business_name": business_name or "Pending",
                    "pdf_path": "",
                    "category_key": category_key,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "status": "awaiting_generation",
                    "delivered_at": None,
                }
                index["reports"].append(queued)
                self._save_index(index)
                logger.warning(
                    f"Fulfillment: no report found for {customer_email}. "
                    f"Queued as '{report_id}' — will deliver on next pipeline run."
                )
                # Send a confirmation email so customer knows payment was received
                self.outreach._send_email(
                    to_email=customer_email,
                    subject=f"Payment confirmed — your report is being prepared",
                    body_text=(
                        f"Hi,\n\n"
                        f"Thank you for your purchase! We've received your payment.\n\n"
                        f"Your full audit report is being generated and will be delivered "
                        f"to this email address within 24 hours.\n\n"
                        f"If you have any questions, just reply to this email.\n\n"
                        f"— Search Sentinel\n"
                        f"sutraflow.org/sentinel"
                    ),
                )
                return {
                    "success": False,
                    "error": "queued_for_generation",
                    "report_id": report_id,
                    "business_name": business_name,
                }

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

    def deliver_queued(self) -> list[dict]:
        """Check for any 'awaiting_generation' reports and attempt delivery.
        Called by orchestrator after report generation to catch queued payments."""
        index = self._load_index()
        results = []
        reports_dir = Path(__file__).parent.parent / "reports"

        queued = [r for r in index["reports"] if r["status"] == "awaiting_generation"]
        for report in queued:
            # Search for a matching PDF
            found_pdf = None
            biz_name = report.get("business_name", "")
            cat_key = report.get("category_key", "")

            if biz_name and biz_name != "Pending":
                biz_slug = biz_name.lower().replace(" ", "_").replace("'", "")
                for pdf in sorted(reports_dir.glob("*.pdf"), reverse=True):
                    if biz_slug in pdf.name.lower():
                        found_pdf = pdf
                        break
            if not found_pdf and cat_key:
                for pdf in sorted(reports_dir.glob("*.pdf"), reverse=True):
                    if cat_key.lower() in pdf.name.lower():
                        found_pdf = pdf
                        break

            if found_pdf:
                report["pdf_path"] = str(found_pdf)
                report["status"] = "pending"
                self._save_index(index)
                result = self.deliver(report["email"], report.get("business_name", ""))
                results.append(result)

        if results:
            logger.info(f"Fulfillment: processed {len(results)} queued deliveries")
        return results

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
