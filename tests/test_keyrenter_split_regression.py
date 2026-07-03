"""Anonymized regression test for the Keyrenter ("John Sample") split.

This locks in the behaviour verified end-to-end on sandbox deposits 148/149:
- multi-property statements map each category to the correct QuickBooks account,
- per-property lines are routed to the correct customer,
- the "Property Reserve" plug posts with no customer (blank entity),
- and the BUG-001 fix holds: the split reuses the existing deposit line's Id so
  QuickBooks replaces the original line instead of appending alongside it.

The data here is fully anonymized (neutral property/customer names, representative
amounts) so it is safe to keep in version control.
It exercises the same code paths as the real statements without any client data.
"""

from datetime import date
from decimal import Decimal
from unittest import TestCase
from unittest.mock import Mock

from app.models import OwnerStatement, StatementEntry
from app.quickbooks import QuickBooksClient


KEYRENTER_SETTINGS = {
    "quickbooks_customer": "Default Customer",
    "description_mode": "blank",
    "blank_entity_categories": ["Property Reserve"],
    "customer_by_property_class": {
        "Property A": "Customer A",
        "Property B": "Customer B",
    },
    "category_accounts": {
        "Rental Income": "Sales:Rental Income",
        "Property management fees": "General business expenses:Property management fees",
        "Leasing Fee": "Commissions & fees:Leasing Fee",
        "Gas Utility": "Gas Utility",
        "Water & sewer": "Utilities:Water & sewer",
        "Transfer funds": "Partner investments:Transfer funds to other property account",
        "Property Reserve": "Partner distributions:Property Cash Reserve",
    },
}


def anonymized_statement() -> OwnerStatement:
    entries = (
        StatementEntry("income", "Rental Income", Decimal("1000.00"), date(2025, 3, 1), "Property A", "Property A"),
        StatementEntry("expense", "Property management fees", Decimal("100.00"), date(2025, 3, 18), "Property A", "Property A"),
        StatementEntry("expense", "Leasing Fee", Decimal("200.00"), date(2025, 3, 17), "Property A", "Property A"),
        StatementEntry("income", "Rental Income", Decimal("800.00"), date(2025, 3, 1), "Property B", "Property B"),
        StatementEntry("expense", "Gas Utility", Decimal("50.00"), date(2025, 3, 8), "Property B", "Property B"),
        StatementEntry("expense", "Water & sewer", Decimal("30.00"), date(2025, 3, 24), "Property B", "Property B"),
        StatementEntry("expense", "Transfer funds", Decimal("20.00"), date(2025, 3, 20), "Property B", "Property B"),
        StatementEntry("income", "Property Reserve", Decimal("5.00")),
    )
    return OwnerStatement(
        property_name="Anonymized Owner",
        statement_month=date(2025, 3, 1),
        entries=entries,
        stated_income=Decimal("1805.00"),
        stated_expenses=Decimal("400.00"),
        stated_net_income=Decimal("1405.00"),
    )


def keyrenter_client() -> QuickBooksClient:
    client = object.__new__(QuickBooksClient)
    account_fqns = list(KEYRENTER_SETTINGS["category_accounts"].values())
    client.all_accounts = Mock(
        return_value=[
            {"Id": str(index), "Name": fqn.split(":")[-1], "FullyQualifiedName": fqn}
            for index, fqn in enumerate(account_fqns, start=100)
        ]
    )
    client.all_customers = Mock(
        return_value=[
            {"Id": "201", "DisplayName": "Default Customer"},
            {"Id": "202", "DisplayName": "Customer A"},
            {"Id": "203", "DisplayName": "Customer B"},
        ]
    )
    return client


def single_line_deposit() -> dict:
    return {
        "Id": "900",
        "SyncToken": "0",
        "TxnDate": "2025-03-31",
        "TotalAmt": 1405.00,
        "DepositToAccountRef": {"value": "35", "name": "Checking"},
        "Line": [
            {
                "Id": "1",
                "Amount": 1405.00,
                "DetailType": "DepositLineDetail",
                "DepositLineDetail": {"AccountRef": {"value": "100"}},
            }
        ],
    }


class KeyrenterSplitRegressionTests(TestCase):
    def setUp(self):
        plan = keyrenter_client().create_split_plan(
            anonymized_statement(), KEYRENTER_SETTINGS, single_line_deposit()
        )
        self.lines = plan["update_payload"]["Line"]
        self.plan = plan

    def _line_by_amount(self, amount: str) -> dict:
        target = Decimal(amount)
        matches = [
            line
            for line in self.lines
            if abs(Decimal(str(line["Amount"])) - target) < Decimal("0.001")
        ]
        self.assertEqual(len(matches), 1, f"expected exactly one line of {amount}")
        return matches[0]

    def test_plan_is_ready_and_reconciles(self):
        self.assertEqual(self.plan["status"], "ready")
        self.assertEqual(len(self.lines), 8)
        total = sum((Decimal(str(line["Amount"])) for line in self.lines), Decimal("0"))
        self.assertEqual(total, Decimal("1405.00"))

    def test_reuses_existing_line_id(self):
        # BUG-001: replace the original line in place, do not append.
        self.assertEqual(self.lines[0]["Id"], "1")
        self.assertTrue(all("Id" not in line for line in self.lines[1:]))

    def test_category_account_mappings(self):
        cases = {
            "-200.00": "Commissions & fees:Leasing Fee",
            "-50.00": "Gas Utility",
            "-30.00": "Utilities:Water & sewer",
            "-20.00": "Partner investments:Transfer funds to other property account",
            "5.00": "Partner distributions:Property Cash Reserve",
        }
        for amount, account_name in cases.items():
            line = self._line_by_amount(amount)
            self.assertEqual(
                line["DepositLineDetail"]["AccountRef"]["name"], account_name
            )

    def test_customer_routing_by_property(self):
        self.assertEqual(
            self._line_by_amount("1000.00")["DepositLineDetail"]["Entity"]["name"],
            "Customer A",
        )
        self.assertEqual(
            self._line_by_amount("800.00")["DepositLineDetail"]["Entity"]["name"],
            "Customer B",
        )

    def test_property_reserve_posts_without_a_customer(self):
        reserve = self._line_by_amount("5.00")
        self.assertNotIn("Entity", reserve["DepositLineDetail"])
