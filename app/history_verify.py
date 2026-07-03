"""Historical verification (offline backtest).

Compares the split our pipeline *predicts* (parser + expert rules + config account
mapping) against the split that was *actually posted* in QuickBooks for past months,
using saved deposit JSON snapshots. It is fully read-only and never contacts QuickBooks.

This is a DIVERGENCE report, not a raw accuracy score: the historical deposits used as
the comparison baseline were themselves flagged "labels need review", so a divergence
means "we differ from what was posted" — each one is a judgement call (our rule may be
the correction, or the line may need a config/rule change). Use it to find where the
rules and the historical books disagree, and why.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .config import property_config
from .expert_rules import apply_expert_history_rules
from .parser import parse_statement_pdf
from .split_audit import (
    build_label_differences,
    line_signature,
    money_string,
)


def _expected_lines(statement, settings) -> list[dict]:
    category_accounts = settings.get("category_accounts", {})
    default_customer = settings.get("quickbooks_customer", "")
    customer_by_class = settings.get("customer_by_property_class", {})
    blank_entity = set(settings.get("blank_entity_categories", []))

    lines = []
    for index, entry in enumerate(statement.entries, start=1):
        account = category_accounts.get(entry.category) or "(unmapped)"
        customer = (
            ""
            if entry.category in blank_entity
            else customer_by_class.get(entry.property_class, default_customer)
        )
        lines.append(
            {
                "line_num": index,
                "amount": money_string(entry.signed_amount),
                "account_id": "",
                "account": account,
                "customer_id": "",
                "customer": customer,
                "source_category": entry.category,
                "source_name": entry.name,
                "source_property_class": entry.property_class,
                "source_description": entry.description,
            }
        )
    return lines


def _actual_lines(deposit_lines: list[dict]) -> list[dict]:
    return [
        {
            "line_num": line.get("line_num"),
            "amount": money_string(line.get("amount", "0")),
            "account_id": "",
            "account": line.get("account", ""),
            "customer_id": "",
            "customer": line.get("received_from", ""),
            "description": line.get("description", ""),
        }
        for line in deposit_lines
    ]


def verify_pair(pdf_path: str | Path, deposit_lines: list[dict], config: dict) -> dict:
    statement = parse_statement_pdf(pdf_path)
    settings = property_config(config, statement.property_name)
    statement, _review = apply_expert_history_rules(statement, settings)
    return compare_split(statement, settings, deposit_lines)


def compare_split(statement, settings: dict, deposit_lines: list[dict]) -> dict:
    """Compare a (post-expert-rules) statement's predicted split to actual deposit lines.

    Separated from PDF parsing so it can be unit-tested with synthetic data.
    """
    expected = _expected_lines(statement, settings)
    actual = _actual_lines(deposit_lines)

    expected_counter = Counter(line_signature(line) for line in expected)
    actual_counter = Counter(line_signature(line) for line in actual)
    matched = sum((expected_counter & actual_counter).values())

    # Per-category match counts (consume actual signatures so duplicates are fair).
    remaining = actual_counter.copy()
    by_category: dict[str, dict] = {}
    for line in expected:
        category = line["source_category"]
        bucket = by_category.setdefault(
            category, {"lines": 0, "matched": 0, "expected_account": line["account"]}
        )
        bucket["lines"] += 1
        signature = line_signature(line)
        if remaining.get(signature, 0) > 0:
            remaining[signature] -= 1
            bucket["matched"] += 1

    differences = build_label_differences(
        actual,
        expected,
        expected_counter - actual_counter,
        actual_counter - expected_counter,
    )

    return {
        "property_name": statement.property_name,
        "statement_month": statement.statement_month.isoformat(),
        "expected_line_count": len(expected),
        "actual_line_count": len(actual),
        "matched_lines": matched,
        "divergence_count": len(differences),
        "by_category": by_category,
        "divergences": differences,
    }


def verify_history(pairs: list[dict], config: dict) -> dict:
    """pairs: list of {"label"?, "pdf", "deposit_file"} (deposit_file = saved qbo-deposit JSON)."""
    months = []
    category_totals: dict[str, dict] = {}
    all_divergences = []
    total_expected = 0
    total_matched = 0

    for pair in pairs:
        deposit_data = json.loads(Path(pair["deposit_file"]).read_text(encoding="utf-8"))
        deposit_lines = deposit_data.get("deposit", {}).get("lines", [])
        result = verify_pair(pair["pdf"], deposit_lines, config)

        label = pair.get("label") or f"{result['property_name']} {result['statement_month']}"
        total_expected += result["expected_line_count"]
        total_matched += result["matched_lines"]

        for category, stats in result["by_category"].items():
            totals = category_totals.setdefault(
                category, {"lines": 0, "matched": 0, "expected_account": stats["expected_account"]}
            )
            totals["lines"] += stats["lines"]
            totals["matched"] += stats["matched"]

        for diff in result["divergences"]:
            all_divergences.append({"month": label, **diff})

        months.append(
            {
                "label": label,
                "deposit_file": pair["deposit_file"],
                "statement_month": result["statement_month"],
                "expected_line_count": result["expected_line_count"],
                "actual_line_count": result["actual_line_count"],
                "matched_lines": result["matched_lines"],
                "divergence_count": result["divergence_count"],
            }
        )

    by_category = [
        {
            "category": category,
            "expected_account": stats["expected_account"],
            "lines": stats["lines"],
            "matched": stats["matched"],
            "match_rate": _rate(stats["matched"], stats["lines"]),
        }
        for category, stats in sorted(category_totals.items())
    ]

    return {
        "mode": "history-verification",
        "note": (
            "Divergence report vs saved historical QBO deposits (read-only, offline). "
            "A divergence means our predicted label differs from what was posted; it is a "
            "judgement call, not necessarily an error on our side."
        ),
        "pair_count": len(pairs),
        "total_expected_lines": total_expected,
        "total_matched_lines": total_matched,
        "overall_match_rate": _rate(total_matched, total_expected),
        "by_month": months,
        "by_category": by_category,
        "divergences": all_divergences,
    }


def verify_history_from_file(pairs_file: str | Path, config: dict) -> dict:
    data = json.loads(Path(pairs_file).read_text(encoding="utf-8"))
    pairs = data["pairs"] if isinstance(data, dict) else data
    return verify_history(pairs, config)


def _rate(matched: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return f"{100 * matched / total:.1f}%"
