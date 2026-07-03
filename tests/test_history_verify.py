"""Regression test for the historical-verification backtest comparison.

Uses synthetic data (neutral names) to confirm that compare_split correctly:
- counts lines that match the posted deposit,
- detects a divergence where our predicted account differs from the posted one.
"""

from datetime import date
from decimal import Decimal
from unittest import TestCase

from app.history_verify import compare_split
from app.models import OwnerStatement, StatementEntry


SETTINGS = {
    "quickbooks_customer": "Default",
    "blank_entity_categories": ["Property Reserve"],
    "customer_by_property_class": {"Property A": "Customer A"},
    "category_accounts": {
        "Rental Income": "Sales:Rental Income",
        "Property management fees": "General business expenses:Property management fees",
        "Property Reserve": "Partner distributions:Property Cash Reserve",
    },
}


def sample_statement() -> OwnerStatement:
    entries = (
        StatementEntry("income", "Rental Income", Decimal("100.00"), date(2025, 3, 1), "Property A", "Property A"),
        StatementEntry("expense", "Property management fees", Decimal("10.00"), date(2025, 3, 18), "Property A", "Property A"),
        StatementEntry("income", "Property Reserve", Decimal("5.00")),
    )
    return OwnerStatement(
        property_name="Owner",
        statement_month=date(2025, 3, 1),
        entries=entries,
        stated_income=Decimal("105.00"),
        stated_expenses=Decimal("10.00"),
        stated_net_income=Decimal("95.00"),
    )


# Posted deposit lines (saved-JSON shape). Rent + mgmt fee match our mapping;
# the Property Reserve line was posted to a DIFFERENT account than we predict.
DEPOSIT_LINES = [
    {"line_num": 1, "amount": "100.0", "account": "Sales:Rental Income", "received_from": "Customer A"},
    {"line_num": 2, "amount": "-10.0", "account": "General business expenses:Property management fees", "received_from": "Customer A"},
    {"line_num": 3, "amount": "5.0", "account": "General business expenses:Property Cash Reserve", "received_from": ""},
]


class HistoryVerifyTests(TestCase):
    def setUp(self):
        self.result = compare_split(sample_statement(), SETTINGS, DEPOSIT_LINES)

    def test_match_and_divergence_counts(self):
        self.assertEqual(self.result["expected_line_count"], 3)
        self.assertEqual(self.result["matched_lines"], 2)
        self.assertEqual(self.result["divergence_count"], 1)

    def test_per_category_matches(self):
        by_category = self.result["by_category"]
        self.assertEqual(by_category["Rental Income"]["matched"], 1)
        self.assertEqual(by_category["Property management fees"]["matched"], 1)
        self.assertEqual(by_category["Property Reserve"]["matched"], 0)

    def test_divergence_reports_both_accounts(self):
        diff = self.result["divergences"][0]
        self.assertEqual(diff["expected_account"], "Partner distributions:Property Cash Reserve")
        self.assertEqual(diff["current_account"], "General business expenses:Property Cash Reserve")
