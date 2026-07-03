from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


APPROVAL_MODE = "screening-correction-approval"


def create_screening_approval(
    review_file: str | Path,
    *,
    approved_by: str = "",
    notes: str = "",
) -> dict:
    """Create an approval artifact from a reviewed screening dry-run file.

    This does not contact QuickBooks and does not apply anything. The approval
    is only useful if a future split --apply run produces the exact same
    screening fingerprint.
    """
    review = json.loads(Path(review_file).read_text(encoding="utf-8"))
    screening = extract_screening(review)
    validation = validate_screening_can_be_approved(screening)
    if not validation["approved"]:
        return {
            "mode": APPROVAL_MODE,
            "status": "not_approved",
            "review_file": str(Path(review_file)),
            "reason": validation["reasons"],
            "screening_summary": screening_summary(screening),
        }

    fingerprint = screening_fingerprint(screening)
    return {
        "mode": APPROVAL_MODE,
        "status": "approved",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "approved_by": approved_by,
        "notes": notes,
        "review_file": str(Path(review_file)),
        "fingerprint": fingerprint,
        "screening_summary": screening_summary(screening),
        "approved_correction_preview": screening.get("correction_preview", []),
        "approval_rules": [
            "Only correction_preview screenings, ready_to_split screenings, or reviewed one-line needs_review screenings can use this approval.",
            "Deposit ID, statement month, totals, line counts, amount checks, warnings, and correction preview must match exactly.",
            "QuickBooks must still have the same current split preview that was reviewed.",
            "The apply command must still include --allow-resplit for already-split correction deposits.",
        ],
    }


def verify_screening_approval(
    screening: dict,
    approval_file: str | Path | None,
    *,
    allow_resplit: bool,
) -> dict:
    """Return whether a blocked correction preview is approved to apply."""
    if not approval_file:
        return {
            "approved": False,
            "reasons": ["No approval file was provided."],
        }

    approval = json.loads(Path(approval_file).read_text(encoding="utf-8"))
    reasons: list[str] = []

    if approval.get("mode") != APPROVAL_MODE:
        reasons.append("Approval file is not a screening correction approval.")
    if approval.get("status") != "approved":
        reasons.append("Approval file status is not approved.")

    validation = validate_screening_can_be_approved(screening)
    if not validation["approved"]:
        reasons.extend(validation["reasons"])

    if screening.get("current_line_count", 0) > 1 and not allow_resplit:
        reasons.append(
            "Approved corrections to an already-split deposit require --allow-resplit."
        )

    current_fingerprint = screening_fingerprint(screening)
    approved_fingerprint = approval.get("fingerprint", {})
    if approved_fingerprint.get("sha256") != current_fingerprint.get("sha256"):
        reasons.append(
            "Current screening no longer matches the approved correction preview."
        )

    return {
        "approved": not reasons,
        "reasons": reasons,
        "approval_file": str(Path(approval_file)),
        "approved_by": approval.get("approved_by", ""),
        "approved_at": approval.get("created_at", ""),
        "current_fingerprint": current_fingerprint,
        "approved_fingerprint": approved_fingerprint,
    }


def validate_screening_can_be_approved(screening: dict) -> dict:
    reasons: list[str] = []
    status = screening.get("status")
    is_correction_preview = status == "correction_preview"
    is_first_split = (
        status in {"ready_to_split", "needs_review"}
        and screening.get("current_line_count") == 1
        and bool(screening.get("planned_split_preview"))
    )
    if not (is_correction_preview or is_first_split):
        reasons.append(
            "Only correction_preview results, ready_to_split results, or reviewed one-line needs_review split results can be approved."
        )
    if not screening.get("total_matches"):
        reasons.append("Deposit total does not match the PDF total.")
    if is_correction_preview and not screening.get("line_amounts_match"):
        reasons.append("Current QuickBooks line amounts do not match the PDF split.")
    if (
        is_correction_preview
        and screening.get("current_line_count") != screening.get("expected_line_count")
    ):
        reasons.append("Current and expected line counts do not match.")
    if is_correction_preview and not screening.get("correction_preview"):
        reasons.append("There is no correction preview to approve.")
    if is_first_split and not screening.get("planned_split_preview"):
        reasons.append("There is no planned split preview to approve.")
    if screening.get("missing_categories"):
        reasons.append(
            "One or more PDF categories still do not have QuickBooks account mappings."
        )
    return {"approved": not reasons, "reasons": reasons}


def extract_screening(review: dict) -> dict:
    if "screening" in review and isinstance(review["screening"], dict):
        return review["screening"]
    if review.get("mode") == "screening":
        return review
    raise ValueError("Could not find a screening section in the review file.")


def screening_summary(screening: dict) -> dict:
    return {
        "deposit_id": screening.get("deposit_id"),
        "statement_month": screening.get("statement_month"),
        "status": screening.get("status"),
        "current_deposit_total": screening.get("current_deposit_total"),
        "expected_pdf_total": screening.get("expected_pdf_total"),
        "total_matches": screening.get("total_matches"),
        "current_line_count": screening.get("current_line_count"),
        "expected_line_count": screening.get("expected_line_count"),
        "line_amounts_match": screening.get("line_amounts_match"),
        "account_customer_labels_match": screening.get(
            "account_customer_labels_match"
        ),
        "expert_warning_count": screening.get("expert_warning_count"),
        "correction_count": len(screening.get("correction_preview", [])),
    }


def screening_fingerprint(screening: dict) -> dict:
    payload = {
        "deposit_id": screening.get("deposit_id"),
        "statement_month": screening.get("statement_month"),
        "current_deposit_total": screening.get("current_deposit_total"),
        "expected_pdf_total": screening.get("expected_pdf_total"),
        "total_matches": screening.get("total_matches"),
        "current_line_count": screening.get("current_line_count"),
        "expected_line_count": screening.get("expected_line_count"),
        "line_amounts_match": screening.get("line_amounts_match"),
        "account_customer_labels_match": screening.get(
            "account_customer_labels_match"
        ),
        "expert_warning_count": screening.get("expert_warning_count"),
        "expert_rule_changes": screening.get("expert_rule_changes", []),
        "expert_rule_warnings": screening.get("expert_rule_warnings", []),
        "missing_categories": screening.get("missing_categories", []),
        "current_split_preview": screening.get("current_split_preview", []),
        "planned_split_preview": screening.get("planned_split_preview", []),
        "correction_preview": screening.get("correction_preview", []),
    }
    canonical = canonical_json(payload)
    return {
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "payload": payload,
    }


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
