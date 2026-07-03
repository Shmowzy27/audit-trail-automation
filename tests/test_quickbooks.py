from decimal import Decimal
from unittest import TestCase
from unittest.mock import Mock

from app.quickbooks import QuickBooksClient, QuickBooksError
from tests.test_parser import SAMPLE
from app.parser import parse_statement_text


SETTINGS = {
    "quickbooks_customer": "500 Oak Street",
    "category_accounts": {
        "Rental Income, Airbnb": "Sales:Rental Income",
        "Job Supplies expense": "Supplies",
        "Listing Site Host Fees": "Advertising & marketing:Listing fees",
        "Office Supplies & Software": "Utilities:Internet & TV services",
        "PM Fees": "General business expenses:Property management fees",
        "Repairs & Maintenance": "Repairs and maintenance",
    },
}


def client_with_fake_lists() -> QuickBooksClient:
    client = object.__new__(QuickBooksClient)
    account_names = list(SETTINGS["category_accounts"].values())
    client.all_accounts = Mock(
        return_value=[
            {"Id": str(index), "Name": name.split(":")[-1], "FullyQualifiedName": name}
            for index, name in enumerate(account_names, start=10)
        ]
    )
    client.all_customers = Mock(
        return_value=[
            {"Id": "42", "DisplayName": "500 Oak Street"}
        ]
    )
    return client


class QuickBooksPlanTests(TestCase):
    def test_plan_replaces_one_line_with_ten_reconciled_lines(self):
        statement = parse_statement_text(SAMPLE)
        deposit = {
            "Id": "123",
            "SyncToken": "0",
            "TxnDate": "2026-05-31",
            "TotalAmt": 1892.33,
            "DepositToAccountRef": {"value": "8", "name": "Checking"},
            "Line": [
                {
                    "Amount": 1892.33,
                    "DetailType": "DepositLineDetail",
                    "DepositLineDetail": {"AccountRef": {"value": "10"}},
                }
            ],
        }
        plan = client_with_fake_lists().create_split_plan(
            statement, SETTINGS, deposit
        )
        lines = plan["update_payload"]["Line"]
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(len(lines), 10)
        self.assertEqual(
            sum((Decimal(str(line["Amount"])) for line in lines), Decimal("0")),
            Decimal("1892.33"),
        )
        self.assertTrue(all(line["DepositLineDetail"]["Entity"]["value"] == "42" for line in lines))

    def test_split_reuses_existing_line_id_to_replace_not_append(self):
        # Regression for the Bug A append: QBO deposit full-updates keep omitted
        # existing lines, so the new split must reuse the existing line's Id to
        # replace it in place instead of appending alongside it.
        statement = parse_statement_text(SAMPLE)
        deposit = {
            "Id": "123",
            "SyncToken": "0",
            "TxnDate": "2026-05-31",
            "TotalAmt": 1892.33,
            "DepositToAccountRef": {"value": "8", "name": "Checking"},
            "Line": [
                {
                    "Id": "1",
                    "Amount": 1892.33,
                    "DetailType": "DepositLineDetail",
                    "DepositLineDetail": {"AccountRef": {"value": "10"}},
                }
            ],
        }
        plan = client_with_fake_lists().create_split_plan(statement, SETTINGS, deposit)
        lines = plan["update_payload"]["Line"]
        self.assertEqual(lines[0]["Id"], "1")
        self.assertTrue(all("Id" not in line for line in lines[1:]))

    def test_override_account_unblocks_an_unmapped_line(self):
        # Edit-to-unblock: if a category has no configured account the plan is
        # refused, but assigning an account per line via overrides lets it build.
        statement = parse_statement_text(SAMPLE)
        target_category = statement.entries[0].category
        settings = {
            **SETTINGS,
            "category_accounts": {
                k: v
                for k, v in SETTINGS["category_accounts"].items()
                if k != target_category
            },
        }
        deposit = {
            "Id": "123",
            "SyncToken": "0",
            "TxnDate": "2026-05-31",
            "TotalAmt": 1892.33,
            "DepositToAccountRef": {"value": "8", "name": "Checking"},
            "Line": [
                {
                    "Amount": 1892.33,
                    "DetailType": "DepositLineDetail",
                    "DepositLineDetail": {"AccountRef": {"value": "10"}},
                }
            ],
        }
        client = client_with_fake_lists()

        # No override → the unmapped category blocks planning.
        with self.assertRaises(QuickBooksError):
            client.create_split_plan(statement, settings, deposit)

        # Override every line of the unmapped category with a real account → builds.
        overrides = {
            index: {"account": "Sales:Rental Income"}
            for index, entry in enumerate(statement.entries, start=1)
            if entry.category == target_category
        }
        plan = client.create_split_plan(
            statement, settings, deposit, overrides=overrides
        )
        self.assertEqual(plan["status"], "ready")

    def test_all_missing_accounts_reported_at_once(self):
        # A fresh company missing several mapped accounts should surface them all in
        # one error, not one per re-run.
        statement = parse_statement_text(SAMPLE)
        settings = {
            **SETTINGS,
            "category_accounts": {
                **SETTINGS["category_accounts"],
                "Rental Income, Airbnb": "Account That Does Not Exist",
                "Repairs & Maintenance": "Another Missing Account",
            },
        }
        deposit = {
            "Id": "123", "SyncToken": "0", "TxnDate": "2026-05-31", "TotalAmt": 1892.33,
            "DepositToAccountRef": {"value": "8"},
            "Line": [{"Amount": 1892.33, "DetailType": "DepositLineDetail",
                      "DepositLineDetail": {"AccountRef": {"value": "10"}}}],
        }
        with self.assertRaises(QuickBooksError) as ctx:
            client_with_fake_lists().create_split_plan(statement, settings, deposit)
        message = str(ctx.exception)
        self.assertIn("Account That Does Not Exist", message)
        self.assertIn("Another Missing Account", message)

    def test_linked_deposit_is_not_modified(self):
        statement = parse_statement_text(SAMPLE)
        deposit = {
            "Id": "123",
            "SyncToken": "0",
            "TxnDate": "2026-05-31",
            "TotalAmt": 1892.33,
            "DepositToAccountRef": {"value": "8"},
            "Line": [
                {
                    "Amount": 1892.33,
                    "DetailType": "DepositLineDetail",
                    "LinkedTxn": [{"TxnId": "9", "TxnType": "Payment"}],
                }
            ],
        }
        with self.assertRaises(QuickBooksError):
            client_with_fake_lists().create_split_plan(statement, SETTINGS, deposit)
