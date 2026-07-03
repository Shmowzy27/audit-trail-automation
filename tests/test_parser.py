from decimal import Decimal
from unittest import TestCase

from app.parser import parse_statement_text


SAMPLE = """500 Oak Street
Owner Statement
April 2026
DATE NAME CLASS MEMO/DESCRIPTION AMOUNT BALANCE
Ordinary Income/Expenses
Income
Rental Income
Rental Income, Airbnb
04/05/2026 guest one 500 Oak Street nights 3 796.10 796.10
04/12/2026 guest two 500 Oak Street nights 3 1,009.00 1,805.10
04/19/2026 guest three 500 Oak Street nights 2 546.30 2,351.40
04/25/2026 guest four 500 Oak Street nights 2 154.80 2,506.20
04/28/2026 guest five 500 Oak Street nights 3 837.00 3,343.20
Total for Rental Income, Airbnb $3,343.20
Total for Rental Income $3,343.20
Total for Income $3,343.20
Expenses
Job Supplies expense
04/30/2026 Sample PM LLC 500 Oak Street 100.00 100.00
Total for Job Supplies expense $100.00
Listing Site Host Fees
Airbnb Host Fee $687.92
Total for Listing Site Host Fees $687.92
Office Supplies & Software
04/30/2026 Sample PM LLC 500 Oak Street Streaming services/wifi 85.00 85.00
Total for Office Supplies & Software $85.00
PM Fees
04/30/2026 Sample PM LLC 500 Oak Street 477.95 477.95
Total for PM Fees $477.95
Repairs & Maintenance
04/30/2026 Sample PM LLC 500 Oak Street Pressure Wash porch, windows and doors 100.00 100.00
Total for Repairs & Maintenance $100.00
Total for Expenses $1,450.87
Net Income $1,892.33
"""


class ParserTests(TestCase):
    def test_statement_reconciles_and_has_ten_split_lines(self):
        statement = parse_statement_text(SAMPLE)
        self.assertEqual(statement.property_name, "500 Oak Street")
        self.assertEqual(len(statement.entries), 10)
        self.assertEqual(statement.calculated_income, Decimal("3343.20"))
        self.assertEqual(statement.calculated_expenses, Decimal("1450.87"))
        self.assertEqual(statement.calculated_net_income, Decimal("1892.33"))

    def test_expenses_are_negative_when_posted(self):
        statement = parse_statement_text(SAMPLE)
        signed = [entry.signed_amount for entry in statement.entries]
        self.assertEqual(signed[-5:], [
            Decimal("-100.00"),
            Decimal("-687.92"),
            Decimal("-85.00"),
            Decimal("-477.95"),
            Decimal("-100.00"),
        ])
