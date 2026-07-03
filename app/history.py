from __future__ import annotations

import re
from pathlib import Path

from .parser import (
    DATE_RANGE,
    KEYRENTER_PROPERTY_HEADER,
    extract_pdf_text,
    parse_date_range_start,
    parse_money,
    parse_statement_pdf,
)


SUMMARY_LINE = re.compile(
    r"^(Beginning Balance|Cash In|Cash Out|Management Fees|Owner Disbursements|"
    r"Ending Cash Balance|Unpaid Bills|Property Reserve|Net Owner Funds|"
    r"Please Remit Balance Due)\s+(-?[\d,]+\.\d{2})$"
)


def audit_statement_history(pdf_paths: list[str | Path]) -> dict:
    months = [audit_statement_pdf(path) for path in pdf_paths]
    months.sort(key=lambda item: item["statement_month"])
    continuity = continuity_checks(months)

    return {
        "mode": "history-audit",
        "status": "ready"
        if all(item["status"] == "ok" for item in continuity)
        else "review",
        "month_count": len(months),
        "months": months,
        "continuity": continuity,
    }


def scan_statement_folder(
    folder: str | Path,
    *,
    start_year: int | None = None,
    end_year: int | None = None,
    extra_pdf_paths: list[str | Path] | None = None,
) -> dict:
    folder_path = Path(folder)
    pdf_paths = sorted(folder_path.rglob("*.pdf"))
    pdf_paths.extend(Path(path) for path in extra_pdf_paths or [])

    parsed = []
    failed = []
    skipped = []
    for path in pdf_paths:
        try:
            month = audit_statement_pdf(path)
        except Exception as exc:  # noqa: BLE001 - report every bad PDF cleanly.
            failed.append({"file": str(path), "error": str(exc)})
            continue

        year = int(month["statement_month"][:4])
        if start_year is not None and year < start_year:
            skipped.append({"file": str(path), "statement_month": month["statement_month"]})
            continue
        if end_year is not None and year > end_year:
            skipped.append({"file": str(path), "statement_month": month["statement_month"]})
            continue
        parsed.append(month)

    parsed.sort(key=lambda item: (item["property_name"], item["statement_month"]))
    continuity = []
    grouped: dict[str, list[dict]] = {}
    for month in parsed:
        grouped.setdefault(month["property_name"], []).append(month)
    for property_name, months in grouped.items():
        property_continuity = continuity_checks(months)
        for item in property_continuity:
            item["owner_statement"] = property_name
        continuity.extend(property_continuity)

    return {
        "mode": "folder-scan",
        "status": "ready"
        if not failed and all(item["status"] == "ok" for item in continuity)
        else "review",
        "folder": str(folder_path),
        "year_filter": {"start_year": start_year, "end_year": end_year},
        "found_pdf_count": len(pdf_paths),
        "parsed_count": len(parsed),
        "failed_count": len(failed),
        "skipped_count": len(skipped),
        "parsed": parsed,
        "failed": failed,
        "skipped": skipped,
        "continuity": continuity,
    }


def audit_statement_pdf(pdf_path: str | Path) -> dict:
    path = Path(pdf_path)
    statement = parse_statement_pdf(path)
    text = extract_pdf_text(path)
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]

    properties = extract_keyrenter_property_summaries(lines)
    reserve_adjustments = [
        {
            "property": entry.property_class,
            "amount": str(entry.signed_amount),
            "account_category": entry.category,
        }
        for entry in statement.entries
        if entry.category == "Property Reserve"
    ]

    return {
        "file": str(path),
        "property_name": statement.property_name,
        "statement_month": statement.statement_month.isoformat(),
        "stated_income": str(statement.stated_income),
        "stated_expenses": str(statement.stated_expenses),
        "stated_net_income": str(statement.stated_net_income),
        "calculated_net_income": str(statement.calculated_net_income),
        "entry_count": len(statement.entries),
        "reserve_adjustments": reserve_adjustments,
        "properties": properties,
    }


def continuity_checks(months: list[dict]) -> list[dict]:
    months = sorted(months, key=lambda item: item["statement_month"])
    continuity = []
    for previous, current in zip(months, months[1:]):
        property_names = sorted(
            set(previous["properties"]) | set(current["properties"])
        )
        for property_name in property_names:
            previous_ending = previous["properties"].get(property_name, {}).get(
                "Ending Cash Balance"
            )
            current_beginning = current["properties"].get(property_name, {}).get(
                "Beginning Balance"
            )
            continuity.append(
                {
                    "from_month": previous["statement_month"],
                    "to_month": current["statement_month"],
                    "property": property_name,
                    "previous_ending_balance": previous_ending,
                    "current_beginning_balance": current_beginning,
                    "status": "ok"
                    if previous_ending == current_beginning
                    else "mismatch",
                }
            )
    return continuity


def extract_keyrenter_property_summaries(lines: list[str]) -> dict:
    properties: dict[str, dict] = {}
    current_property: str | None = None
    in_summary = False

    for line in lines:
        property_match = KEYRENTER_PROPERTY_HEADER.match(line)
        if property_match:
            current_property = property_match.group("property").strip()
            properties[current_property] = {}
            in_summary = False
            continue

        if current_property and line == "Property Cash Summary":
            in_summary = True
            continue

        if current_property and line == "Transactions":
            in_summary = False
            continue

        if current_property and in_summary:
            summary_match = SUMMARY_LINE.match(line)
            if summary_match:
                properties[current_property][summary_match.group(1)] = str(
                    parse_money(summary_match.group(2))
                )

    return properties


def statement_month_from_lines(lines: list[str]) -> str:
    for line in lines:
        date_range = DATE_RANGE.search(line)
        if date_range:
            return parse_date_range_start(date_range.group("start")).replace(
                day=1
            ).isoformat()
    raise ValueError("Could not find a statement date range.")
