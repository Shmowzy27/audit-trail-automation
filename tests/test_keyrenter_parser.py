from unittest import TestCase

from app.expert_rules import apply_expert_history_rules
from app.parser import parse_statement_text


SAMPLE = """
Keyrenter Springfield
John Sample
Owner Statement
15 Elm St #2 Mar 01, 2026 - Mar 31, 2026
Consolidated Summary (5 properties)
14, 16 Cedar St - 14, 16 Cedar St, Springfield, TN 37000
Transactions
Beginning Cash Balance as of 03/01/2026 282.37
03/01/2026 14 Cedar St E520-9710 14 Cedar St - Rent Income - March 2026 1,050.00 1,332.37
03/17/2026 eCheck D741-D200 Management fees - Management fees for 03/2026 90.00 1,242.37
03/31/2026 eCheck F3D1-81C0 Owner Draw - Owner payment for 03/2026 1,242.37 0.00
Ending Cash Balance 0.00
"""


class KeyrenterParserTests(TestCase):
    def test_keyrenter_statement_uses_date_range_and_owner_payment_total(self):
        statement = parse_statement_text(SAMPLE)

        self.assertEqual(statement.property_name, "John Sample")
        self.assertEqual(statement.statement_month.isoformat(), "2026-03-01")
        self.assertEqual(str(statement.stated_net_income), "1242.37")
        self.assertEqual(len(statement.entries), 3)
        # The parser no longer categorizes Keyrenter lines — it emits raw kind-based
        # categories and leaves classification to the expert-rules layer (single source
        # of truth). Property Reserve is set by the balance-adjustment logic, not categorization.
        self.assertEqual(
            [entry.category for entry in statement.entries],
            ["Other income", "Other expense", "Property Reserve"],
        )

    def test_expert_rules_categorize_parsed_keyrenter_lines(self):
        statement = parse_statement_text(SAMPLE)
        categorized, _review = apply_expert_history_rules(statement, {})
        self.assertEqual(
            [entry.category for entry in categorized.entries],
            ["Rental Income", "Property management fees", "Property Reserve"],
        )


# Anonymized multi-property statement (neutral names) — exercises property switching,
# per-property reserve adjustment, owner-draw exclusion, and income/expense detection.
MULTI_SAMPLE = """
Keyrenter Springfield
Anonymized Owner
Owner Statement
Consolidated Summary (2 properties) Mar 01, 2026 - Mar 31, 2026
Property A - Property A, Springfield, TN 37000
Transactions
Beginning Cash Balance as of 03/01/2026 0.00
03/01/2026 Property A E520-9710 Property A - Rent Income - March 2026 500.00 500.00
03/17/2026 eCheck D741-D200 Management fees - Management fees for 03/2026 50.00 450.00
03/31/2026 eCheck F3D1-81C0 Owner Draw - Owner payment for 03/2026 450.00 0.00
Ending Cash Balance 0.00
Property B - Property B, Springfield, TN 37000
Transactions
Beginning Cash Balance as of 03/01/2026 25.71
03/01/2026 Property B A1B2-C3D4 Property B - Rent Income - March 2026 800.00 825.71
03/10/2026 Check 2169 Property B - Gas - Vacant Utility 30.00 795.71
03/31/2026 eCheck E5F6-7890 Owner Draw - Owner payment for 03/2026 795.71 0.00
Ending Cash Balance 0.00
"""


# Regression for BUG-019: the PDF interleaves rows, so a transaction's category must
# come from its OWN line — a rent/management row next to a "Transfer from ..." row must
# not be read as a transfer — while a bare amount row (just a reference) must still pick
# up its description from the adjacent wrapped lines (e.g. a security deposit).
ALIGNMENT_SAMPLE = """
Keyrenter Springfield
Regression Owner
Owner Statement
Consolidated Summary (1 property) Mar 01, 2026 - Mar 31, 2026
Property Z - Property Z, Springfield, TN 37000
Transactions
Beginning Cash Balance as of 03/01/2026 0.00
03/01/2026 Property Z E520-9710 Property Z - Rent Income - March 2026 500.00 500.00
03/02/2026 Transfer Transfer from Property Y Ln. 100.00 600.00
03/05/2026 eCheck A1B2-C3D4 Management fees - Management fees for 03/2026 60.00 540.00
Bank Tenant Q, Property Z: Security
03/10/2026 #77777777 250.00 790.00
Transfer Deposit Transfer
03/31/2026 eCheck F3D1-81C0 Owner Draw - Owner payment for 03/2026 790.00 0.00
Ending Cash Balance 0.00
"""


class KeyrenterDescriptionAlignmentTests(TestCase):
    def test_category_comes_from_the_lines_own_row(self):
        statement = parse_statement_text(ALIGNMENT_SAMPLE)
        categorized, _ = apply_expert_history_rules(statement, {})
        by_amount = {str(e.amount): e.category for e in categorized.entries}
        # Rent sits directly above a transfer row — must stay rent, not transfer.
        self.assertEqual(by_amount["500.00"], "Rental Income")
        # The real transfer row is still a transfer.
        self.assertEqual(by_amount["100.00"], "Transfer funds")
        # Management fee sits next to the deposit's wrap line — must stay a mgmt fee.
        self.assertEqual(by_amount["60.00"], "Property management fees")
        # Bare "#77777777" amount row still absorbs its wrapped "Security ... Deposit
        # Transfer" description from the adjacent non-dated lines.
        self.assertEqual(by_amount["250.00"], "Security deposits")


class KeyrenterMultiPropertyTests(TestCase):
    def setUp(self):
        self.statement = parse_statement_text(MULTI_SAMPLE)

    def test_owner_month_and_reconciled_total(self):
        self.assertEqual(self.statement.property_name, "Anonymized Owner")
        self.assertEqual(self.statement.statement_month.isoformat(), "2026-03-01")
        self.assertEqual(str(self.statement.stated_net_income), "1245.71")

    def test_owner_draws_excluded_and_five_entries(self):
        amounts = [str(e.amount) for e in self.statement.entries]
        self.assertEqual(len(self.statement.entries), 5)
        self.assertNotIn("450.00", amounts)   # Property A owner draw
        self.assertNotIn("795.71", amounts)   # Property B owner draw

    def test_per_property_attribution(self):
        rent_b = [e for e in self.statement.entries if str(e.amount) == "800.00"][0]
        self.assertEqual(rent_b.property_class, "Property B")

    def test_per_property_reserve_adjustment(self):
        reserve = [e for e in self.statement.entries if e.category == "Property Reserve"]
        self.assertEqual(len(reserve), 1)
        self.assertEqual(str(reserve[0].amount), "25.71")

    def test_income_expense_detection_via_balance(self):
        kinds = {str(e.amount): e.kind for e in self.statement.entries}
        self.assertEqual(kinds["500.00"], "income")   # rent
        self.assertEqual(kinds["50.00"], "expense")   # management fee
        self.assertEqual(kinds["30.00"], "expense")   # gas

    def test_expert_rules_categorize_all_lines(self):
        categorized, _ = apply_expert_history_rules(self.statement, {})
        self.assertEqual(
            [e.category for e in categorized.entries],
            ["Rental Income", "Property management fees", "Rental Income", "Gas Utility", "Property Reserve"],
        )
