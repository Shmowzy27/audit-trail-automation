import json
import tempfile
from pathlib import Path
from unittest import TestCase

from app.approvals import create_screening_approval, verify_screening_approval


def screening(status: str = "correction_preview") -> dict:
    return {
        "mode": "screening",
        "deposit_id": "532",
        "statement_month": "2025-08-01",
        "status": status,
        "current_deposit_total": "4689.89",
        "expected_pdf_total": "4689.89",
        "total_matches": True,
        "current_line_count": 38,
        "expected_line_count": 38,
        "line_amounts_match": True,
        "account_customer_labels_match": False,
        "expert_warning_count": 1,
        "expert_rule_changes": [],
        "expert_rule_warnings": [{"type": "mixed_pdf_context", "line": 1}],
        "missing_categories": [],
        "current_split_preview": [{"amount": "-140.11", "account": "Sales:Rental Income"}],
        "planned_split_preview": [
            {
                "amount": "-140.11",
                "account": "Partner investments:Transfer funds to other property account",
            }
        ],
        "correction_preview": [
            {
                "amount": "-140.11",
                "current_account": "Sales:Rental Income",
                "expected_account": "Partner investments:Transfer funds to other property account",
            }
        ],
    }


class ApprovalTests(TestCase):
    def test_approval_must_match_current_screening_exactly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            review_file = Path(temp_dir) / "screening.json"
            approval_file = Path(temp_dir) / "approval.json"
            review_file.write_text(
                json.dumps({"screening": screening()}), encoding="utf-8"
            )

            approval = create_screening_approval(
                review_file, approved_by="Reviewer", notes="reviewed"
            )
            approval_file.write_text(json.dumps(approval), encoding="utf-8")

            verified = verify_screening_approval(
                screening(), approval_file, allow_resplit=True
            )
            self.assertTrue(verified["approved"])

            changed = screening()
            changed["correction_preview"][0]["expected_account"] = "Cash"
            rejected = verify_screening_approval(
                changed, approval_file, allow_resplit=True
            )
            self.assertFalse(rejected["approved"])

    def test_approval_requires_allow_resplit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            review_file = Path(temp_dir) / "screening.json"
            approval_file = Path(temp_dir) / "approval.json"
            review_file.write_text(
                json.dumps({"screening": screening()}), encoding="utf-8"
            )
            approval_file.write_text(
                json.dumps(create_screening_approval(review_file)), encoding="utf-8"
            )

            verified = verify_screening_approval(
                screening(), approval_file, allow_resplit=False
            )
            self.assertFalse(verified["approved"])

    def test_reviewed_first_split_with_warnings_can_be_approved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            review_file = Path(temp_dir) / "screening.json"
            approval_file = Path(temp_dir) / "approval.json"
            first_split = screening(status="needs_review")
            first_split["current_line_count"] = 1
            first_split["current_split_preview"] = [
                {"amount": "4689.89", "account": "Sales:Rental Income"}
            ]
            first_split["correction_preview"] = []
            review_file.write_text(
                json.dumps({"screening": first_split}), encoding="utf-8"
            )
            approval_file.write_text(
                json.dumps(create_screening_approval(review_file)), encoding="utf-8"
            )

            verified = verify_screening_approval(
                first_split, approval_file, allow_resplit=False
            )
            self.assertTrue(verified["approved"])
