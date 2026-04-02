"""
Fulfillment Agent — generates and delivers PDF audit reports after Stripe payment.

NEW FLOW (v2 — April 2026):
  - Orchestrator registers ALERT DATA (not PDFs) after sending teasers
  - Webhook server calls deliver() after Stripe payment confirmation
  - deliver() generates the PDF on-demand, then emails it to the customer
  - This avoids generating hundreds of PDFs that nobody pays for

Index format (pending_reports.json):
{
  "reports": [
    {
      "id": "abc123",
      "email": "owner@business.com",
      "business_name": "Ace Plumbing",
      "category_key": "losangeles_ca_plumber",
      "alert_data": { ... full alert dict ... },
      "pdf_path": "",             # empty until PDF is generated on payment
      "created_at": "2026-04-01T...",
      "status": "pending|delivered|failed|awaiting_generation",
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

    def register_alerts(self, contacted: list[dict]) -> list[str]:
        """Register alerts (NOT PDFs) for post-payment fulfillment.

        Called by the orchestrator after sending teaser emails.
        Stores the full alert data so we can generate the PDF on-demand
        when the customer pays.
        """
        index = self._load_index()
        report_ids = []

        for entry in contacted:
            report_id = uuid.uuid4().hex[:12]
            index["reports"].append({
                "id": report_id,
                "email": entry["email"],
                "business_name": entry["business_name"],
                "category_key": entry.get("category_key", ""),
                "alert_data": entry.get("alert_data", {}),
                "pdf_path": "",  # no PDF yet — generated on payment
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "pending",
                "delivered_at": None,
            })
            report_ids.append(report_id)

        self._save_index(index)
        logger.info(f"Fulfillment: registered {len(report_ids)} alerts for post-payment delivery")
        return report_ids

    # Keep backward compat for any existing index entries with pdf_path
    def register_reports(self, contacted: list[dict]) -> list[str]:
        """Legacy: register reports that already have a pdf_path."""
        return self.register_alerts(contacted)

    def _generate_pdf_for_alert(self, alert_data: dict) -> str | None:
        """Generate a PDF on-demand for a single alert.

        Returns the PDF file path, or None on failure.
        Only called when a customer has actually paid.
        """
        if not alert_data:
            logger.warning("Fulfillment: no alert_data to generate PDF from")
            return None

        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent))
            import config
            from agents.report_agent import ReportAgent

            reporter = ReportAgent(
                api_key=config.ANTHROPIC_API_KEY,
                reports_dir=config.REPORTS_DIR,
                model=config.CLAUDE_MODEL,
            )
            pdf_path = reporter.generate_audit(alert_data)
            if pdf_path:
                logger.info(f"Fulfillment: generated PDF on-demand: {pdf_path}")
                return str(pdf_path)
            else:
                logger.error("Fulfillment: report_agent returned None for PDF")
                return None
        except Exception as e:
            logger.error(f"Fulfillment: PDF generation failed: {e}", exc_info=True)
            return None

    def deliver(self, customer_email: str, business_name: str = "",
                category_key: str = "") -> dict:
        """Find pending alert for this email, generate PDF on-demand, and deliver.

        Called by the webhook server after Stripe payment confirmation.
        Flow: find matching alert → generate PDF → email PDF to customer.

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
                # If it has a PDF already, re-deliver
                if report.get("pdf_path") and Path(report["pdf_path"]).exists():
                    logger.info(f"Fulfillment: re-delivering existing report to {customer_email}")
                    matching = [report]
                    report["status"] = "pending"
                elif report.get("alert_data"):
                    # Has alert data but no PDF — generate fresh
                    logger.info(f"Fulfillment: regenerating PDF for returning customer {customer_email}")
                    matching = [report]
                    report["status"] = "pending"
                    report["pdf_path"] = ""

            if not matching:
                # FALLBACK 2: Search reports directory for any PDF matching business name
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
                    report_id = uuid.uuid4().hex[:12]
                    report = {
                        "id": report_id,
                        "email": customer_email,
                        "business_name": business_name or "Your Business",
                        "pdf_path": str(found_pdf),
                        "category_key": category_key,
                        "alert_data": {},
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "status": "pending",
                        "delivered_at": None,
                    }
                    index["reports"].append(report)
                    self._save_index(index)
                    matching = [report]
                    logger.info(f"Fulfillment: found existing PDF {found_pdf.name} for {customer_email}")

            if not matching:
                # FALLBACK 3: Queue for delivery — next pipeline run will pick it up
                report_id = uuid.uuid4().hex[:12]
                queued = {
                    "id": report_id,
                    "email": customer_email,
                    "business_name": business_name or "Pending",
                    "pdf_path": "",
                    "category_key": category_key,
                    "alert_data": {},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "status": "awaiting_generation",
                    "delivered_at": None,
                }
                index["reports"].append(queued)
                self._save_index(index)
                logger.warning(
                    f"Fulfillment: no alert data found for {customer_email}. "
                    f"Queued as '{report_id}' — will deliver on next pipeline run."
                )
                self.outreach._send_email(
                    to_email=customer_email,
                    subject="Payment confirmed — your report is being prepared",
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

        # ── Generate PDF on-demand if we don't have one yet ──────────────────
        pdf_path_str = report.get("pdf_path", "")
        if not pdf_path_str or not Path(pdf_path_str).exists():
            alert_data = report.get("alert_data", {})
            if alert_data:
                logger.info(f"Fulfillment: generating PDF on-demand for {report['business_name']}...")
                pdf_path_str = self._generate_pdf_for_alert(alert_data)
                if pdf_path_str:
                    report["pdf_path"] = pdf_path_str
                    self._save_index(index)
                else:
                    report["status"] = "failed"
                    self._save_index(index)
                    logger.error(f"Fulfillment: on-demand PDF generation failed for {report['id']}")
                    return {"success": False, "error": "pdf_generation_failed"}
            else:
                logger.error(f"Fulfillment: no alert_data and no PDF for {report['id']}")
                report["status"] = "failed"
                self._save_index(index)
                return {"success": False, "error": "no_alert_data"}

        pdf_path = Path(pdf_path_str)
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
        Called by orchestrator to catch queued payments from between runs."""
        index = self._load_index()
        results = []

        queued = [r for r in index["reports"] if r["status"] == "awaiting_generation"]
        for report in queued:
            alert_data = report.get("alert_data", {})

            if alert_data:
                # Generate PDF on-demand
                pdf_path = self._generate_pdf_for_alert(alert_data)
                if pdf_path:
                    report["pdf_path"] = pdf_path
                    report["status"] = "pending"
                    self._save_index(index)
                    result = self.deliver(report["email"], report.get("business_name", ""))
                    results.append(result)
                continue

            # No alert data — try to find a matching PDF in reports/
            reports_dir = Path(__file__).parent.parent / "reports"
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

    def deliver_subscriber_report(self, customer_email: str) -> dict:
        """Generate and deliver PDF for an active subscriber's rank drop.

        Called after the weekly pipeline sends the subscriber a drop notification.
        This runs asynchronously (e.g., via a background job or the webhook server).
        """
        index = self._load_index()
        email_lower = customer_email.lower().strip()

        # Find the most recent subscriber report entry
        matching = [
            r for r in index["reports"]
            if r["email"].lower().strip() == email_lower
            and r.get("alert_data")
            and r["status"] == "pending"
        ]

        if not matching:
            return {"success": False, "error": "no_pending_alert"}

        report = matching[-1]
        alert_data = report["alert_data"]

        # Generate PDF
        pdf_path = self._generate_pdf_for_alert(alert_data)
        if not pdf_path:
            report["status"] = "failed"
            self._save_index(index)
            return {"success": False, "error": "pdf_generation_failed"}

        report["pdf_path"] = pdf_path
        self._save_index(index)

        # Deliver via email with PDF attached
        success = self.outreach.send_subscriber_report_email(
            to_email=report["email"],
            business_name=report["business_name"],
            alert=alert_data,
            pdf_path=Path(pdf_path),
        )

        if success:
            report["status"] = "delivered"
            report["delivered_at"] = datetime.now(timezone.utc).isoformat()
        else:
            report["status"] = "failed"

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
            "awaiting": sum(1 for r in reports if r["status"] == "awaiting_generation"),
        }
