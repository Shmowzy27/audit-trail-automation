from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Literal


EntryKind = Literal["income", "expense"]


@dataclass(frozen=True)
class StatementEntry:
    kind: EntryKind
    category: str
    amount: Decimal
    transaction_date: date | None = None
    name: str = ""
    property_class: str = ""
    description: str = ""

    @property
    def signed_amount(self) -> Decimal:
        return self.amount if self.kind == "income" else -self.amount

    def to_dict(self) -> dict:
        data = asdict(self)
        data["amount"] = str(self.amount)
        data["signed_amount"] = str(self.signed_amount)
        data["transaction_date"] = (
            self.transaction_date.isoformat() if self.transaction_date else None
        )
        return data


@dataclass(frozen=True)
class OwnerStatement:
    property_name: str
    statement_month: date
    entries: tuple[StatementEntry, ...]
    stated_income: Decimal
    stated_expenses: Decimal
    stated_net_income: Decimal

    @property
    def calculated_income(self) -> Decimal:
        return sum(
            (entry.amount for entry in self.entries if entry.kind == "income"),
            Decimal("0"),
        )

    @property
    def calculated_expenses(self) -> Decimal:
        return sum(
            (entry.amount for entry in self.entries if entry.kind == "expense"),
            Decimal("0"),
        )

    @property
    def calculated_net_income(self) -> Decimal:
        return sum((entry.signed_amount for entry in self.entries), Decimal("0"))

    def validate(self) -> None:
        checks = (
            ("income", self.calculated_income, self.stated_income),
            ("expenses", self.calculated_expenses, self.stated_expenses),
            ("net income", self.calculated_net_income, self.stated_net_income),
        )
        failures = [
            f"{label}: calculated {actual} but statement says {expected}"
            for label, actual, expected in checks
            if abs(actual - expected) > Decimal("0.01")
        ]
        if failures:
            raise ValueError("Statement does not reconcile: " + "; ".join(failures))

    def to_dict(self) -> dict:
        return {
            "property_name": self.property_name,
            "statement_month": self.statement_month.isoformat(),
            "stated_income": str(self.stated_income),
            "stated_expenses": str(self.stated_expenses),
            "stated_net_income": str(self.stated_net_income),
            "calculated_net_income": str(self.calculated_net_income),
            "entries": [entry.to_dict() for entry in self.entries],
        }
