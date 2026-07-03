"""Tests for expert-rule category decisions changed during the 2021-2024 accountant alignment.

The accountant never used a top-level "Contract labor" account: plumbing/water work went
to Repairs & maintenance (Plumbing), and electric bills to Utilities:Electricity. These
tests lock in that routing.
"""

from decimal import Decimal
from unittest import TestCase

from app.expert_rules import (
    assess_line_confidence,
    build_duplicate_and_context_warnings,
    recommended_category_for_entry,
)
from app.models import StatementEntry


def entry(description: str, category: str = "Other expense", kind: str = "expense") -> StatementEntry:
    return StatementEntry(
        kind=kind,
        category=category,
        amount=Decimal("100.00"),
        property_class="14, 16 Cedar St",
        description=description,
    )


class ExpertRuleRoutingTests(TestCase):
    def test_water_heater_routes_to_plumbing(self):
        category, _ = recommended_category_for_entry(
            entry("14 Cedar St - water heater valve replacement")
        )
        self.assertEqual(category, "Plumbing")

    def test_plumbing_routes_to_plumbing(self):
        category, _ = recommended_category_for_entry(
            entry("drainage pipe repair - plumbing service")
        )
        self.assertEqual(category, "Plumbing")

    def test_water_damage_routes_to_repairs_not_contract_labor(self):
        category, _ = recommended_category_for_entry(
            entry("water damage remediation and dry up water")
        )
        self.assertEqual(category, "Repairs & Maintenance")

    def test_electric_bill_routes_to_electricity(self):
        category, _ = recommended_category_for_entry(
            entry("EPB electric utility - electricity vacant")
        )
        self.assertEqual(category, "Electricity")


class SecurityDepositAndTransferTests(TestCase):
    """Bank-transfer lines: tenant security deposits vs inter-property transfers.

    Grounded in the accountant's 2021-2024 postings — QBO deposit 434 (a tenant
    deposit -> Security deposits) and deposit 176 (a "Security Deposit Transfer"
    *between properties* -> Transfer funds).
    """

    def test_interleaved_security_deposit_routes_to_security_deposits(self):
        # PDF extraction splits "Security Deposit Transfer" with date/ref/amount
        # tokens; the words must still be recognized (the deposit-151 bug).
        category, _ = recommended_category_for_entry(
            entry(
                "Bank Ryan N. Linnemann, 88 Birch Lane: Security 09/25/2025 "
                "#12492832 1,850.00 2,150.00 Transfer Deposit Transfer",
                category="Other income",
                kind="income",
            )
        )
        self.assertEqual(category, "Security deposits")

    def test_contiguous_security_deposit_routes_to_security_deposits(self):
        category, _ = recommended_category_for_entry(
            entry(
                "Bank Gracyn L. Gordon, Steven C. Gordon, 90 Birch Lane Ln: "
                "06/26/2024 #10041600 2,520.00 2,520.00 Transfer Security Deposit Transfer",
                category="Other income",
                kind="income",
            )
        )
        self.assertEqual(category, "Security deposits")

    def test_interproperty_security_deposit_transfer_routes_to_transfer_funds(self):
        # Same "Security Deposit Transfer" wording, but it is an internal transfer
        # between the owner's own properties -> Transfer funds, not a tenant deposit.
        category, _ = recommended_category_for_entry(
            entry(
                "Transfer Ln: Security Deposit Transfer 03/08/2023 Transfer "
                "Transfer from 88 Birch Lane 490.08 2,866.84 Hixson Utility",
                category="Other income",
                kind="income",
            )
        )
        self.assertEqual(category, "Transfer funds")

    def test_transfer_to_other_property_routes_to_transfer_funds(self):
        category, _ = recommended_category_for_entry(
            entry(
                "receipt 03/08/2023 Transfer Transfer to 90 Birch Lane Ln 490.08 1,709.92",
                category="Other expense",
                kind="expense",
            )
        )
        self.assertEqual(category, "Transfer funds")


class LeaseRenewalFeeTests(TestCase):
    """Lease renewal fees -> Leasing Fee (accountant's 2023/2024 practice,
    QBO deposits 226 and 398)."""

    def test_renewal_fee_routes_to_leasing_fee(self):
        category, _ = recommended_category_for_entry(
            entry("06/18/2026 eCheck 580B-B220 20 Cedar St - Lease Renewal Fee - Renewal Fee 250.00 1,862.00")
        )
        self.assertEqual(category, "Leasing Fee")

    def test_interleaved_renewal_text_still_routes(self):
        # The 02-26 packet splits the phrase: "...Lease Renewal Fee - Renewal 02/13/2026..."
        category, _ = recommended_category_for_entry(
            entry("Keyrenter 12 Cedar St - Lease Renewal Fee - Renewal 02/13/2026 eCheck A318-A400 250.00 2,360.00")
        )
        self.assertEqual(category, "Leasing Fee")


class SafetyDeviceMaintenanceTests(TestCase):
    """Detector/filter supply-and-install -> Repairs & Maintenance (QBO dep 402,
    'rekey locks, co detectors'); cleaning-context filter lines stay Cleaning."""

    def test_smoke_detectors_route_to_repairs(self):
        category, _ = recommended_category_for_entry(
            entry("Keyrenter 05/28/2026 eCheck BE14-3640 in items: Supply/Intall 3 Smoke Detectors, Air filter pack, 221.00 399.97 Maintenance")
        )
        self.assertEqual(category, "Repairs & Maintenance")

    def test_cleaning_context_filters_stay_cleaning(self):
        category, _ = recommended_category_for_entry(
            entry("Other Cleaning and Maintenance - Move In items: air filter pack 41.00")
        )
        self.assertEqual(category, "Cleaning")


class DuplicateAmountWarningTests(TestCase):
    """The duplicate-amount warning should fire only for a possible mislabel within
    one property, not for amounts that legitimately recur across properties/units.
    """

    @staticmethod
    def _e(amount, category, prop, kind="expense"):
        return StatementEntry(kind, category, Decimal(amount), property_class=prop)

    def _dup_warnings(self, entries):
        return [
            w
            for w in build_duplicate_and_context_warnings(tuple(entries))
            if w["type"] == "duplicate_or_similar_amounts"
        ]

    def test_same_amount_across_different_properties_is_not_flagged(self):
        entries = [
            self._e("540.00", "Rental Income", "14, 16 Cedar St", kind="income"),
            self._e("540.00", "Rental Income", "18, 20 Cedar St", kind="income"),
        ]
        self.assertEqual(self._dup_warnings(entries), [])

    def test_repeated_same_category_on_one_property_is_not_flagged(self):
        # Three legitimate $55 mowings at one property (distinct checks/dates).
        entries = [self._e("55.00", "Mowing Service", "14, 16 Cedar St") for _ in range(3)]
        self.assertEqual(self._dup_warnings(entries), [])

    def test_same_amount_same_property_different_category_is_flagged(self):
        entries = [
            self._e("100.00", "Cleaning", "14, 16 Cedar St"),
            self._e("100.00", "Plumbing", "14, 16 Cedar St"),
        ]
        warnings = self._dup_warnings(entries)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["property_class"], "14, 16 Cedar St")


class ConfidenceScoringTests(TestCase):
    def _entry(self, category: str) -> StatementEntry:
        return StatementEntry("expense", category, Decimal("100.00"), property_class="P")

    def test_unclassified_is_low(self):
        level, _ = assess_line_confidence(self._entry("Other expense"), warned=False, rule_fired=False)
        self.assertEqual(level, "low")

    def test_known_category_with_rule_is_high(self):
        level, _ = assess_line_confidence(self._entry("Rental Income"), warned=False, rule_fired=True)
        self.assertEqual(level, "high")

    def test_warned_line_is_medium(self):
        level, _ = assess_line_confidence(self._entry("Rental Income"), warned=True, rule_fired=True)
        self.assertEqual(level, "medium")

    def test_category_without_accountant_precedent_is_medium(self):
        # Accounting fees has no 2021-2024 precedent, so it caps at medium.
        level, _ = assess_line_confidence(self._entry("Accounting fees"), warned=False, rule_fired=True)
        self.assertEqual(level, "medium")
