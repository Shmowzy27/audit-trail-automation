from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pdfplumber

from .models import OwnerStatement, StatementEntry


MONEY = r"\(?-?\$?[\d,]+\.\d{2}\)?"
DATED_ROW = re.compile(
    rf"^(?P<date>\d{{2}}/\d{{2}}/\d{{4}})\s+"
    rf"(?P<body>.*?)\s+(?P<amount>{MONEY})\s+(?P<balance>{MONEY})$"
)
UNDATED_AMOUNT = re.compile(rf"^(?P<description>.+?)\s+(?P<amount>{MONEY})$")
TOTAL_LINE = re.compile(rf"^(?P<label>Total for .+?|Net Income)\s+(?P<amount>{MONEY})$")
MONTH_LINE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{4}$"
)
DATE_RANGE = re.compile(
    r"\b(?P<start>(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
    r"\s+\d{1,2},\s+\d{4})\s*-\s*"
    r"(?P<end>(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
    r"\s+\d{1,2},\s+\d{4})\b",
    flags=re.IGNORECASE,
)
KEYRENTER_PROPERTY_HEADER = re.compile(
    r"^(?P<property>.+?)\s+-\s+.+?,\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?$"
)
KEYRENTER_BEGINNING_BALANCE = re.compile(
    rf"^Beginning Cash Balance as of \d{{2}}/\d{{2}}/\d{{4}}\s+(?P<amount>{MONEY})$"
)
KEYRENTER_ENDING_BALANCE = re.compile(
    rf"^Ending Cash Balance\s+(?P<amount>{MONEY})$"
)

ADDRESS_ALIASES = {
    "avenue": "ave",
    "street": "st",
    "road": "rd",
    "drive": "dr",
    "boulevard": "blvd",
    "lane": "ln",
    "court": "ct",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
}


def parse_money(value: str) -> Decimal:
    cleaned = value.strip().replace("$", "").replace(",", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    amount = Decimal(cleaned)
    return -amount if negative else amount


def normalize_words(value: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", value.lower())
    return [ADDRESS_ALIASES.get(word, word) for word in words]


def split_around_property(body: str, property_name: str) -> tuple[str, str, str]:
    """Split a row body into name, property/class, and description."""
    body_parts = list(re.finditer(r"\S+", body))
    body_words = [ADDRESS_ALIASES.get(re.sub(r"\W", "", m.group()).lower(), "") for m in body_parts]
    target = normalize_words(property_name)

    for start in range(0, len(body_words) - len(target) + 1):
        if body_words[start : start + len(target)] == target:
            char_start = body_parts[start].start()
            char_end = body_parts[start + len(target) - 1].end()
            return (
                body[:char_start].strip(),
                body[char_start:char_end].strip(),
                body[char_end:].strip(),
            )

    # Some statements abbreviate the heading but spell out the row's address.
    address_number = target[0] if target else ""
    if address_number:
        address_match = re.search(
            rf"\b{re.escape(address_number)}\b.*?\b(?:North|South|East|West|N|S|E|W)\b",
            body,
            flags=re.IGNORECASE,
        )
        if address_match:
            return (
                body[: address_match.start()].strip(),
                address_match.group().strip(),
                body[address_match.end() :].strip(),
            )

    return body.strip(), "", ""


def extract_pdf_text(pdf_path: str | Path) -> str:
    with pdfplumber.open(str(pdf_path)) as pdf:
        return "\n".join(
            page.extract_text(x_tolerance=2, y_tolerance=3) or "" for page in pdf.pages
        )


def parse_statement_pdf(pdf_path: str | Path) -> OwnerStatement:
    return parse_statement_text(extract_pdf_text(pdf_path))


def parse_statement_text(text: str) -> OwnerStatement:
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]

    try:
        owner_index = lines.index("Owner Statement")
    except ValueError as exc:
        raise ValueError("This PDF does not contain an 'Owner Statement' heading.") from exc

    if owner_index == 0:
        raise ValueError("Could not find the property name above 'Owner Statement'.")
    property_name = lines[owner_index - 1]

    month_text = next((line for line in lines[owner_index + 1 :] if MONTH_LINE.match(line)), None)
    if not month_text:
        date_range_line = next(
            (line for line in lines[owner_index + 1 :] if DATE_RANGE.search(line)),
            None,
        )
        if date_range_line:
            return parse_keyrenter_statement_lines(
                lines, owner_index=owner_index, date_range_line=date_range_line
            )
        raise ValueError("Could not find the statement month.")
    statement_month = datetime.strptime(month_text, "%B %Y").date().replace(day=1)

    section: str | None = None
    category: str | None = None
    entries: list[StatementEntry] = []
    stated_income = stated_expenses = stated_net = None

    ignored = {
        "Ordinary Income/Expenses",
        "Rental Income",
        "DATE NAME CLASS MEMO/DESCRIPTION AMOUNT BALANCE",
    }

    for line in lines:
        if line == "Income":
            section, category = "income", None
            continue
        if line == "Expenses":
            section, category = "expense", None
            continue

        total_match = TOTAL_LINE.match(line)
        if total_match:
            label = total_match.group("label")
            amount = abs(parse_money(total_match.group("amount")))
            if label == "Total for Income":
                stated_income = amount
            elif label == "Total for Expenses":
                stated_expenses = amount
            elif label == "Net Income":
                stated_net = parse_money(total_match.group("amount"))
            continue

        if section is None or line in ignored or line == month_text or line == property_name:
            continue

        dated_match = DATED_ROW.match(line)
        if dated_match:
            if not category:
                raise ValueError(f"Found a transaction before its category: {line}")
            name, property_class, description = split_around_property(
                dated_match.group("body"), property_name
            )
            entries.append(
                StatementEntry(
                    kind=section,
                    category=category,
                    amount=abs(parse_money(dated_match.group("amount"))),
                    transaction_date=datetime.strptime(
                        dated_match.group("date"), "%m/%d/%Y"
                    ).date(),
                    name=name,
                    property_class=property_class,
                    description=description,
                )
            )
            continue

        undated_match = UNDATED_AMOUNT.match(line)
        if undated_match and section == "income" and category:
            description = undated_match.group("description").strip()
            entries.append(
                StatementEntry(
                    kind="income",
                    category=category,
                    amount=abs(parse_money(undated_match.group("amount"))),
                    description="" if description == category else description,
                )
            )
            continue

        if undated_match and section == "expense" and category:
            description = undated_match.group("description").strip()
            entry_category = nashville_expense_category(category, description)
            entries.append(
                StatementEntry(
                    kind="expense",
                    category=entry_category,
                    amount=abs(parse_money(undated_match.group("amount"))),
                    description="" if description == entry_category else description,
                )
            )
            continue

        # Remaining non-total text inside a section is a category heading.
        if not line.startswith("Total for "):
            category = line

    missing = [
        label
        for label, value in (
            ("Total for Income", stated_income),
            ("Total for Expenses", stated_expenses),
            ("Net Income", stated_net),
        )
        if value is None
    ]
    if missing:
        raise ValueError("Missing statement totals: " + ", ".join(missing))
    if not entries:
        raise ValueError("No income or expense rows were found.")

    statement = OwnerStatement(
        property_name=property_name,
        statement_month=statement_month,
        entries=tuple(entries),
        stated_income=stated_income,
        stated_expenses=stated_expenses,
        stated_net_income=stated_net,
    )
    statement.validate()
    return statement


def nashville_expense_category(current_category: str, description: str) -> str:
    lowered = description.lower()
    if (
        lowered in {"listing site host fees", "airbnb host fee"}
        or "airbnb host fee" in lowered
        or "airbnb host fees" in lowered
    ):
        return "Listing Site Host Fees"
    return current_category


def parse_keyrenter_statement_lines(
    lines: list[str], *, owner_index: int, date_range_line: str
) -> OwnerStatement:
    """Parse Keyrenter consolidated owner statements.

    These statements use a date range such as "Mar 01, 2026 - Mar 31, 2026"
    instead of the older "April 2026" heading.

    The QuickBooks split should mirror the detailed property transactions:
    rental income is positive, expenses/transfers are negative, owner-payment
    rows are excluded, and a beginning-minus-ending balance adjustment is added
    only when it is not zero.
    """
    owner_name = lines[owner_index - 1]
    range_match = DATE_RANGE.search(date_range_line)
    if not range_match:
        raise ValueError("Could not find the Keyrenter statement date range.")

    statement_month = parse_date_range_start(range_match.group("start")).replace(day=1)
    entries: list[StatementEntry] = []
    reserve_entries: list[StatementEntry] = []
    current_property = ""
    beginning_balance: Decimal | None = None
    ending_balance: Decimal | None = None
    running_balance: Decimal | None = None

    def finish_property() -> None:
        nonlocal beginning_balance, ending_balance, running_balance
        if current_property and beginning_balance is not None and ending_balance is not None:
            adjustment = beginning_balance - ending_balance
            if abs(adjustment) > Decimal("0.01"):
                reserve_entries.append(
                    StatementEntry(
                        kind="income" if adjustment > 0 else "expense",
                        category="Property Reserve",
                        amount=abs(adjustment),
                        property_class=current_property,
                    )
                )
        beginning_balance = ending_balance = running_balance = None

    for index, line in enumerate(lines[owner_index + 1 :], start=owner_index + 1):
        property_match = KEYRENTER_PROPERTY_HEADER.match(line)
        if property_match:
            finish_property()
            current_property = property_match.group("property").strip()
            continue

        beginning_match = KEYRENTER_BEGINNING_BALANCE.match(line)
        if beginning_match and current_property:
            beginning_balance = parse_money(beginning_match.group("amount"))
            running_balance = beginning_balance
            continue

        ending_match = KEYRENTER_ENDING_BALANCE.match(line)
        if ending_match and current_property:
            ending_balance = parse_money(ending_match.group("amount"))
            continue

        dated_match = DATED_ROW.match(line)
        if not dated_match:
            continue

        amount = abs(parse_money(dated_match.group("amount")))
        new_balance = parse_money(dated_match.group("balance"))
        body = dated_match.group("body")

        if running_balance is not None:
            if abs((running_balance + amount) - new_balance) <= Decimal("0.01"):
                kind = "income"
            elif abs((running_balance - amount) - new_balance) <= Decimal("0.01"):
                kind = "expense"
            else:
                kind = keyrenter_fallback_kind(body)
        else:
            kind = keyrenter_fallback_kind(body)
        running_balance = new_balance

        if "Owner Draw - Owner payment" not in body:
            # Describe this row from its own text. Only a *bare* amount row — one
            # whose body is just a check/reference with no inline "Category -
            # detail" phrase (e.g. a security-deposit or move-out row whose
            # description wraps onto adjacent lines) — folds in its neighboring
            # non-dated wrap lines. A self-describing body (one that already
            # contains " - ") is used alone, so a neighboring transaction's text
            # can't relabel it. Dated neighbors are always excluded: they are
            # separate transactions, and folding them in mislabels this line
            # (a rent receipt beside a "Transfer from ..." row read as a transfer).
            context_lines = [line]
            if " - " not in body:
                prev_index = index - 1
                if prev_index >= owner_index + 1 and not DATED_ROW.match(lines[prev_index]):
                    context_lines.insert(0, lines[prev_index])
                next_index = index + 1
                if next_index < len(lines) and not DATED_ROW.match(lines[next_index]):
                    context_lines.append(lines[next_index])
            context = " ".join(context_lines)
            entries.append(
                StatementEntry(
                    kind=kind,
                    category="Other income" if kind == "income" else "Other expense",
                    amount=amount,
                    transaction_date=datetime.strptime(
                        dated_match.group("date"), "%m/%d/%Y"
                    ).date(),
                    name=current_property,
                    property_class=current_property,
                    description=keyrenter_description(context),
                )
            )

    finish_property()
    reserve_total = sum(
        (
            entry.signed_amount
            for entry in reserve_entries
        ),
        Decimal("0"),
    )
    if abs(reserve_total) > Decimal("0.01"):
        entries.append(
            StatementEntry(
                kind="income" if reserve_total > 0 else "expense",
                category="Property Reserve",
                amount=abs(reserve_total),
            )
        )

    if not entries:
        raise ValueError("No Keyrenter transaction rows were found.")

    stated_income = sum(
        (entry.amount for entry in entries if entry.kind == "income"), Decimal("0")
    )
    stated_expenses = sum(
        (entry.amount for entry in entries if entry.kind == "expense"), Decimal("0")
    )
    stated_net = stated_income - stated_expenses
    statement = OwnerStatement(
        property_name=owner_name,
        statement_month=statement_month,
        entries=tuple(entries),
        stated_income=stated_income,
        stated_expenses=stated_expenses,
        stated_net_income=stated_net,
    )
    statement.validate()
    return statement


def keyrenter_fallback_kind(body: str) -> str:
    lowered = body.lower()
    if "receipt" in lowered or "transfer from" in lowered or "security deposit transfer" in lowered:
        return "income"
    return "expense"


def keyrenter_description(context: str) -> str:
    # Keep descriptions compact. QuickBooks gets unhappy with noisy multi-line
    # PDF extraction, so normalize spaces and let the property/customer/account
    # carry the important detail.
    return " ".join(context.split())[:250]


def parse_date_range_start(value: str):
    normalized = re.sub(r"^Sept\b", "Sep", value.strip(), flags=re.IGNORECASE)
    for pattern in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(normalized, pattern).date()
        except ValueError:
            pass
    raise ValueError(f"Could not parse date: {value}")
