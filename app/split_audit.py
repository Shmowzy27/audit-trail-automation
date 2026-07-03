from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config import property_config
from .expert_rules import apply_expert_history_rules
from .parser import parse_statement_pdf
from .quickbooks import QuickBooksClient, deposit_total


def audit_split(
    pdf_path: str | Path,
    config: dict,
    *,
    deposit_id: str | None = None,
) -> dict:
    """Compare the current QuickBooks split to the PDF-derived expected split.

    This is intentionally read-only. It builds the same replacement split plan
    used by the normal dry run, but it never sends an update back to QuickBooks.
    """
    statement = parse_statement_pdf(pdf_path)
    settings = property_config(config, statement.property_name)
    statement, expert_rule_review = apply_expert_history_rules(statement, settings)
    missing_categories = sorted(
        {entry.category for entry in statement.entries}
        - set(settings.get("category_accounts", {}))
    )
    if missing_categories:
        return {
            "mode": "qbo-read-only",
            "status": "needs_review",
            "reason": (
                "One or more PDF lines do not have a confident QuickBooks account "
                "mapping. The audit stopped before comparing to QuickBooks so the "
                "line can be reviewed instead of guessed."
            ),
            "statement_month": statement.statement_month.isoformat(),
            "expected_pdf_total": str(statement.stated_net_income),
            "missing_categories": missing_categories,
            "expert_rule_review": expert_rule_review,
            "unmapped_pdf_lines": [
                summarize_source_entry(index, entry)
                for index, entry in enumerate(statement.entries, start=1)
                if entry.category in missing_categories
            ],
        }
    quickbooks = QuickBooksClient()
    deposit = (
        quickbooks.get_deposit(deposit_id)
        if deposit_id
        else quickbooks.find_matching_deposit(
            statement, settings, config.get("deposit_search", {})
        )
    )
    plan = quickbooks.create_split_plan(
        statement,
        settings,
        deposit,
        allow_resplit=True,
    )

    current_lines = [
        summarize_current_line(line)
        for line in deposit.get("Line", [])
        if line.get("DetailType") == "DepositLineDetail"
    ]
    expected_lines = [
        summarize_expected_line(line, source_entry)
        for line, source_entry in zip(
            plan["update_payload"]["Line"], statement.entries, strict=True
        )
    ]

    current_counter = Counter(line_signature(line) for line in current_lines)
    expected_counter = Counter(line_signature(line) for line in expected_lines)
    amount_counter_current = Counter(amount_signature(line) for line in current_lines)
    amount_counter_expected = Counter(amount_signature(line) for line in expected_lines)

    missing_from_qbo = expected_counter - current_counter
    extra_or_different_in_qbo = current_counter - expected_counter

    return {
        "mode": "qbo-read-only",
        "status": "ready",
        "deposit_id": deposit.get("Id"),
        "statement_month": statement.statement_month.isoformat(),
        "current_deposit_total": str(deposit_total(deposit)),
        "expected_pdf_total": str(statement.stated_net_income),
        "total_matches": money_equal(deposit_total(deposit), statement.stated_net_income),
        "current_line_count": len(current_lines),
        "expected_line_count": len(expected_lines),
        "line_amounts_match": amount_counter_current == amount_counter_expected,
        "account_customer_labels_match": current_counter == expected_counter,
        "expert_rule_review": expert_rule_review,
        "summary": build_summary(
            amount_counter_current == amount_counter_expected,
            current_counter == expected_counter,
        ),
        "expected_missing_from_qbo": expand_counter(missing_from_qbo),
        "qbo_extra_or_different": expand_counter(extra_or_different_in_qbo),
        "label_differences": build_label_differences(
            current_lines,
            expected_lines,
            missing_from_qbo,
            extra_or_different_in_qbo,
        ),
        "current_qbo_lines": current_lines,
        "expected_pdf_lines": expected_lines,
    }


def build_summary(amounts_match: bool, labels_match: bool) -> str:
    if amounts_match and labels_match:
        return "Current QuickBooks split matches the PDF-derived expected split."
    if amounts_match and not labels_match:
        return (
            "The line amounts match, but one or more account/customer labels differ."
        )
    if not amounts_match and labels_match:
        return "The labels match, but line amounts/counts differ."
    return "Both amounts/counts and labels differ."


def summarize_current_line(line: dict[str, Any]) -> dict:
    detail = line.get("DepositLineDetail", {})
    entity = detail.get("Entity", {})
    account = detail.get("AccountRef", {})
    return {
        "line_num": line.get("LineNum"),
        "amount": money_string(line.get("Amount", 0)),
        "account_id": ref_value(account),
        "account": ref_name(account),
        "customer_id": ref_value(entity),
        "customer": ref_name(entity),
        "description": line.get("Description", ""),
    }


def summarize_expected_line(line: dict[str, Any], source_entry) -> dict:
    detail = line.get("DepositLineDetail", {})
    entity = detail.get("Entity", {})
    account = detail.get("AccountRef", {})
    return {
        "line_num": line.get("LineNum"),
        "amount": money_string(line.get("Amount", 0)),
        "account_id": ref_value(account),
        "account": ref_name(account),
        "customer_id": ref_value(entity),
        "customer": ref_name(entity),
        "description": line.get("Description", ""),
        "source_kind": source_entry.kind,
        "source_category": source_entry.category,
        "source_amount": money_string(source_entry.signed_amount),
        "source_transaction_date": (
            source_entry.transaction_date.isoformat()
            if source_entry.transaction_date
            else ""
        ),
        "source_name": source_entry.name,
        "source_property_class": source_entry.property_class,
        "source_description": source_entry.description,
    }


def summarize_source_entry(index: int, entry) -> dict:
    return {
        "line_num": index,
        "amount": money_string(entry.signed_amount),
        "source_kind": entry.kind,
        "source_category": entry.category,
        "source_transaction_date": (
            entry.transaction_date.isoformat() if entry.transaction_date else ""
        ),
        "source_name": entry.name,
        "source_property_class": entry.property_class,
        "source_description": entry.description,
    }


def expand_counter(counter: Counter) -> list[dict]:
    rows = []
    for (amount, account_key, customer_key), count in sorted(counter.items()):
        rows.append(
            {
                "amount": amount,
                "account_key": account_key,
                "customer_key": customer_key,
                "count": count,
            }
        )
    return rows


def build_label_differences(
    current_lines: list[dict],
    expected_lines: list[dict],
    missing_from_qbo: Counter,
    extra_or_different_in_qbo: Counter,
) -> list[dict]:
    """Pair each expected-but-missing line with the current QBO line by amount.

    This avoids fragile ad-hoc reporting scripts that match by amount only and
    accidentally pull the wrong PDF description when the same amount appears in
    nearby rows.
    """
    used_current: set[int] = set()
    used_expected: set[int] = set()
    current_extras: list[dict] = []

    for key in repeated_counter_keys(extra_or_different_in_qbo):
        line = pop_line_by_signature(current_lines, key, used_current)
        if line:
            current_extras.append(line)

    differences: list[dict] = []
    used_extra_indices: set[int] = set()

    for key in repeated_counter_keys(missing_from_qbo):
        expected = pop_line_by_signature(expected_lines, key, used_expected) or {}
        amount = key[0]
        current = pop_line_by_amount(current_extras, amount, used_extra_indices) or {}
        differences.append(
            {
                "amount": amount,
                "current_account": current.get("account", ""),
                "current_customer": current.get("customer", ""),
                "expected_account": expected.get("account", ""),
                "expected_customer": expected.get("customer", ""),
                "expected_source_category": expected.get("source_category", ""),
                "expected_source_name": expected.get("source_name", ""),
                "expected_source_property_class": expected.get(
                    "source_property_class", ""
                ),
                "pdf_description": expected.get("source_description", ""),
            }
        )

    return differences


def repeated_counter_keys(counter: Counter) -> list[tuple[str, str, str]]:
    keys: list[tuple[str, str, str]] = []
    for key, count in sorted(counter.items()):
        keys.extend([key] * count)
    return keys


def pop_line_by_signature(
    lines: list[dict],
    key: tuple[str, str, str],
    used_indices: set[int],
) -> dict | None:
    for index, line in enumerate(lines):
        if index not in used_indices and line_signature(line) == key:
            used_indices.add(index)
            return line
    return None


def pop_line_by_amount(
    lines: list[dict],
    amount: str,
    used_indices: set[int],
) -> dict | None:
    for index, line in enumerate(lines):
        if index not in used_indices and amount_signature(line) == amount:
            used_indices.add(index)
            return line
    return None


def line_signature(line: dict) -> tuple[str, str, str]:
    return (
        amount_signature(line),
        compare_key(line.get("account_id", ""), line.get("account", "")),
        compare_key(line.get("customer_id", ""), line.get("customer", "")),
    )


def amount_signature(line: dict) -> str:
    return money_string(line.get("amount", "0"))


def money_equal(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) <= Decimal("0.01")


def money_string(value) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


def normalize_label(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def compare_key(ref_id: str, label: str) -> str:
    if ref_id:
        return f"id:{ref_id}"
    return f"name:{normalize_label(label)}"


def ref_name(ref: dict[str, Any]) -> str:
    return str(ref.get("name") or ref.get("Name") or ref.get("value") or "")


def ref_value(ref: dict[str, Any]) -> str:
    return str(ref.get("value") or ref.get("Value") or "")
