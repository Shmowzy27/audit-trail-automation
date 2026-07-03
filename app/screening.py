from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any

from .quickbooks import deposit_total
from .split_audit import (
    amount_signature,
    build_label_differences,
    line_signature,
    summarize_current_line,
    summarize_expected_line,
)


def build_screening_report(
    *,
    statement,
    expert_rule_review: dict,
    deposit: dict,
    screening_plan: dict,
    missing_categories: list[str] | None = None,
    planning_error: str | None = None,
    unmapped_preview: list | None = None,
) -> dict:
    current_lines = [
        summarize_current_line(line)
        for line in deposit.get("Line", [])
        if line.get("DetailType") == "DepositLineDetail"
    ]
    reasons: list[str] = []
    warning_count = len(expert_rule_review.get("warnings", []))

    if warning_count:
        reasons.append(
            f"{warning_count} expert-rule warning(s) need review before applying."
        )

    if missing_categories:
        reasons.append(
            "Missing QuickBooks account mapping for: " + ", ".join(missing_categories)
        )

    if planning_error:
        reasons.append(planning_error)

    base_report: dict[str, Any] = {
        "mode": "screening",
        "deposit_id": deposit.get("Id"),
        "statement_month": statement.statement_month.isoformat(),
        "current_deposit_total": str(deposit_total(deposit)),
        "expected_pdf_total": str(statement.stated_net_income),
        "total_matches": money_equal(deposit_total(deposit), statement.stated_net_income),
        "current_line_count": len(current_lines),
        "expert_warning_count": warning_count,
        "expert_rule_changes": expert_rule_review.get("changes", []),
        "expert_rule_warnings": expert_rule_review.get("warnings", []),
        "missing_categories": missing_categories or [],
        "reasons": reasons,
        "current_split_preview": compact_lines(current_lines),
        "planned_split_preview": [],
        "correction_preview": [],
    }

    if missing_categories or planning_error:
        return {
            **base_report,
            # Still surface every line (with unmapped accounts left blank) so the
            # reviewer can assign an account in the UI and re-check to unblock.
            "planned_split_preview": unmapped_preview or [],
            "status": "blocked",
            "apply_allowed": False,
            "summary": "Screening stopped before planning the split.",
        }

    if screening_plan.get("status") != "ready":
        status = screening_plan.get("status", "blocked")
        return {
            **base_report,
            "status": status,
            "apply_allowed": status == "already_split" and not reasons,
            "summary": f"Screening plan status is {status}.",
        }

    expected_lines = [
        summarize_expected_line(line, source_entry)
        for line, source_entry in zip(
            screening_plan["update_payload"]["Line"],
            statement.entries,
            strict=True,
        )
    ]

    current_counter = Counter(line_signature(line) for line in current_lines)
    expected_counter = Counter(line_signature(line) for line in expected_lines)
    amount_counter_current = Counter(amount_signature(line) for line in current_lines)
    amount_counter_expected = Counter(amount_signature(line) for line in expected_lines)
    missing_from_qbo = expected_counter - current_counter
    extra_or_different_in_qbo = current_counter - expected_counter
    label_differences = build_label_differences(
        current_lines,
        expected_lines,
        missing_from_qbo,
        extra_or_different_in_qbo,
    )

    report = {
        **base_report,
        "expected_line_count": len(expected_lines),
        "line_amounts_match": amount_counter_current == amount_counter_expected,
        "account_customer_labels_match": current_counter == expected_counter,
        "planned_split_preview": compact_lines(expected_lines, include_source=True),
        "correction_preview": label_differences,
    }

    if not report["total_matches"]:
        reasons.append("Deposit total does not match the PDF net income.")
        return {
            **report,
            "status": "blocked",
            "apply_allowed": False,
            "summary": "Blocked because the deposit total does not match the PDF.",
        }

    if len(current_lines) == 1:
        return {
            **report,
            "status": "ready_to_split" if not reasons else "needs_review",
            "apply_allowed": not reasons,
            "summary": (
                "Screening passed; deposit is unsplit and ready for the planned split."
                if not reasons
                else "Screening found review warnings before splitting."
            ),
        }

    if current_counter == expected_counter:
        return {
            **report,
            "status": "already_split_matches" if not reasons else "needs_review",
            "apply_allowed": not reasons,
            "summary": (
                "Current QuickBooks split already matches the PDF-derived split."
                if not reasons
                else "Current split matches, but review warnings are present."
            ),
        }

    if amount_counter_current == amount_counter_expected:
        reasons.append(
            "Current split amounts match, but one or more labels differ. Review the correction preview first."
        )
        return {
            **report,
            "status": "correction_preview",
            "apply_allowed": False,
            "summary": (
                "Safe correction preview created. No apply is allowed until the label differences are reviewed."
            ),
        }

    reasons.append(
        "Current split amounts do not match the PDF-derived split. Review manually."
    )
    return {
        **report,
        "status": "blocked",
        "apply_allowed": False,
        "summary": "Blocked because the current split amounts do not match the PDF.",
    }


def build_unmapped_preview(statement, settings: dict, overrides: dict | None = None) -> list[dict]:
    """Display-only per-line rows for the review table, tolerant of unmapped lines.

    When a line's category has no account (and the reviewer hasn't overridden one),
    the QBO-validated plan can't be built — but we still want to show the whole split
    so the reviewer can pick an account for the blank line and re-check. ``account``
    is ``None`` for such lines; everything else mirrors what the plan would display.
    No QuickBooks calls: account/customer names come from config + overrides.
    """
    category_accounts = settings.get("category_accounts", {})
    customer_by_class = settings.get("customer_by_property_class", {})
    default_customer = settings.get("quickbooks_customer") or ""
    blank_entity = set(settings.get("blank_entity_categories", []))
    line_overrides = {int(k): v for k, v in (overrides or {}).items()}

    rows: list[dict] = []
    for index, entry in enumerate(statement.entries, start=1):
        override = line_overrides.get(index, {})
        account = override.get("account") or category_accounts.get(entry.category)
        if override.get("customer"):
            customer = override["customer"]
        elif entry.category in blank_entity:
            customer = None
        else:
            customer = customer_by_class.get(entry.property_class, default_customer)
        description = " - ".join(
            part
            for part in (
                entry.name,
                entry.description,
                entry.transaction_date.isoformat() if entry.transaction_date else "",
            )
            if part
        )
        rows.append(
            {
                "line_num": index,
                "amount": str(entry.signed_amount),
                "account": account,
                "customer": customer,
                "description": description,
                "source_category": entry.category,
                "source_property_class": entry.property_class,
                "source_description": entry.description,
                "needs_account": account is None,
            }
        )
    return rows


def compact_lines(lines: list[dict], *, include_source: bool = False) -> list[dict]:
    compact = []
    for line in lines:
        row = {
            "line_num": line.get("line_num"),
            "amount": line.get("amount"),
            "account": line.get("account"),
            "customer": line.get("customer"),
            "description": line.get("description", ""),
        }
        if include_source:
            row.update(
                {
                    "source_category": line.get("source_category", ""),
                    "source_property_class": line.get("source_property_class", ""),
                    "source_description": line.get("source_description", ""),
                }
            )
        compact.append(row)
    return compact


def money_equal(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) <= Decimal("0.01")
