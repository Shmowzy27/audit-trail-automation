"""Tests for the blocked-but-showable split preview (edit-to-unblock).

When a line has no account mapping the QBO-validated plan can't be built, but the
review UI still needs to show every line so the reviewer can assign an account and
re-check. `build_unmapped_preview` produces those rows.
"""

from app.parser import parse_statement_text
from app.screening import build_unmapped_preview
from tests.test_parser import SAMPLE


def test_unmapped_lines_appear_with_no_account():
    statement = parse_statement_text(SAMPLE)
    settings = {"category_accounts": {}, "quickbooks_customer": "Owner"}
    rows = build_unmapped_preview(statement, settings)
    assert len(rows) == len(statement.entries)
    assert all(r["account"] is None and r["needs_account"] for r in rows)
    # Amounts are still shown (signed) so the split reads correctly while blocked.
    assert all(r["amount"] for r in rows)


def test_override_account_clears_needs_account():
    statement = parse_statement_text(SAMPLE)
    settings = {"category_accounts": {}, "quickbooks_customer": "Owner"}
    rows = build_unmapped_preview(
        statement, settings, overrides={1: {"account": "Sales:Rental Income"}}
    )
    assert rows[0]["account"] == "Sales:Rental Income"
    assert rows[0]["needs_account"] is False


def test_configured_category_populates_account():
    statement = parse_statement_text(SAMPLE)
    category = statement.entries[0].category
    settings = {
        "category_accounts": {category: "Sales:Rental Income"},
        "quickbooks_customer": "Owner",
    }
    rows = build_unmapped_preview(statement, settings)
    assert rows[0]["account"] == "Sales:Rental Income"
    assert rows[0]["needs_account"] is False
