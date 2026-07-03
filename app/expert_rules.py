from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace

from .models import OwnerStatement, StatementEntry


EXPERT_RULE_SOURCE = (
    "expert-posted QuickBooks split patterns from the 2021-2024 history review"
)

# Categories that did NOT appear in the accountant's 2021-2024 Keyrenter postings.
# They came from newer (2025) statements, so they have no historical precedent to
# lean on and are scored at most "medium" confidence until confirmed.
NO_ACCOUNTANT_PRECEDENT = {
    "Move Out Refund",
    "Door Installation",
    "Dishwasher Installation",
    "Insurance",
    "Liability insurance",
    "Accounting fees",
}


def assess_line_confidence(
    entry: StatementEntry, *, warned: bool, rule_fired: bool
) -> tuple[str, str]:
    """Return (level, driver) for a single categorized line.

    Deliberately ordinal (high/medium/low) with an explicit reason rather than a
    made-up percentage: we only claim the confidence we can actually justify.
    """
    if entry.category in {"Other income", "Other expense"}:
        return "low", "No expert-history rule matched this line; it must be reviewed."
    if warned:
        return "medium", "A duplicate or mixed-context warning flags this line for review."
    if entry.category in NO_ACCOUNTANT_PRECEDENT:
        return (
            "medium",
            "This category has no 2021-2024 accountant precedent; confirm the account.",
        )
    if rule_fired:
        return "high", "An expert-history rule set this category, with accountant precedent."
    return "high", "Category came from the statement and maps to a known accountant account."


def apply_expert_history_rules(
    statement: OwnerStatement, property_settings: dict
) -> tuple[OwnerStatement, dict]:
    """Apply conservative label rules learned from historical QBO splits.

    The parser reads what the PDF says. This layer is deliberately separate:
    it represents the bookkeeping pattern we learned from older, expert-posted
    QuickBooks deposits. It only changes known categories and leaves uncertain
    rows as-is so the normal missing-mapping checks still stop the run.
    """
    if property_settings.get("use_expert_history_rules", True) is False:
        return statement, {
            "enabled": False,
            "source": EXPERT_RULE_SOURCE,
            "changes": [],
            "warnings": [],
        }

    updated_entries: list[StatementEntry] = []
    changes: list[dict] = []
    warnings: list[dict] = []
    rule_fired: dict[int, bool] = {}

    for index, entry in enumerate(statement.entries, start=1):
        recommended_category, reason = recommended_category_for_entry(entry)
        rule_fired[index] = recommended_category is not None
        if recommended_category and recommended_category != entry.category:
            updated_entries.append(replace(entry, category=recommended_category))
            changes.append(
                {
                    "line": index,
                    "amount": str(entry.signed_amount),
                    "property_class": entry.property_class,
                    "old_category": entry.category,
                    "new_category": recommended_category,
                    "reason": reason,
                }
            )
            continue

        updated_entries.append(entry)
        if entry.category in {"Other income", "Other expense"}:
            warnings.append(
                {
                    "type": "unclassified_line",
                    "line": index,
                    "amount": str(entry.signed_amount),
                    "property_class": entry.property_class,
                    "category": entry.category,
                    "reason": (
                        "No expert-history rule matched this line. Review it "
                        "before applying to QuickBooks."
                    ),
                }
            )

    updated_statement = replace(statement, entries=tuple(updated_entries))
    updated_statement.validate()
    warnings.extend(build_duplicate_and_context_warnings(updated_statement.entries))
    confidence = score_confidence(updated_statement.entries, warnings, rule_fired)
    return updated_statement, {
        "enabled": True,
        "source": EXPERT_RULE_SOURCE,
        "changes": changes,
        "warnings": warnings,
        "confidence": confidence,
    }


def warned_line_numbers(warnings: list[dict]) -> set[int]:
    numbers: set[int] = set()
    for warning in warnings:
        if "lines" in warning:
            for line in warning["lines"]:
                numbers.add(line.get("line"))
        elif "line" in warning:
            numbers.add(warning.get("line"))
    return numbers


def score_confidence(
    entries: tuple[StatementEntry, ...],
    warnings: list[dict],
    rule_fired: dict[int, bool],
) -> dict:
    """Per-line confidence (high/medium/low) plus an aggregate summary.

    Confidence is surfaced for review and for the future approval dashboard; it does
    not change categorization or the apply path.
    """
    warned = warned_line_numbers(warnings)
    lines: list[dict] = []
    counts = {"high": 0, "medium": 0, "low": 0}
    for index, entry in enumerate(entries, start=1):
        level, driver = assess_line_confidence(
            entry, warned=index in warned, rule_fired=rule_fired.get(index, False)
        )
        counts[level] += 1
        lines.append(
            {
                "line": index,
                "category": entry.category,
                "amount": str(entry.signed_amount),
                "confidence": level,
                "driver": driver,
            }
        )
    if counts["low"]:
        overall = "low"
    elif counts["medium"]:
        overall = "needs_review"
    else:
        overall = "high"
    return {
        "overall": overall,
        "summary": counts,
        "review_line_count": counts["medium"] + counts["low"],
        "lines": lines,
    }


def recommended_category_for_entry(entry: StatementEntry) -> tuple[str | None, str]:
    text = normalized_entry_text(entry)
    rent_position = first_position(text, "rent income")
    transfer_position = first_position(text, "transfer transfer to", "transfer transfer from")

    if (
        entry.kind == "income"
        and entry.category == "Rental Income"
        and rent_position >= 0
        and (transfer_position == -1 or rent_position < transfer_position)
    ):
        return (
            "Rental Income",
            (
                "Protection rule: when rent income appears before neighboring transfer "
                "text in the PDF extraction, keep the line as rental income."
            ),
        )

    if entry.kind == "expense" and entry.category == "Property management fees" and (
        "management fees - management fees" in text
        or ("echeck" in text and "management fees" in text)
    ):
        return (
            "Property management fees",
            (
                "Protection rule: Keyrenter eCheck management-fee rows stay management "
                "fees even when nearby PDF extraction includes transfer text."
            ),
        )

    if is_tenant_security_deposit(text) and not is_interproperty_transfer(text):
        return (
            "Security deposits",
            (
                "Tenant security deposit received via bank transfer -> Security deposits "
                "(liability), matching the accountant's 2021-2024 practice (e.g. the 2024 "
                "$2,520 move-in deposit, QBO deposit 434). PDF extraction can interleave "
                "'Security Deposit Transfer' with dates/refs/amounts, so the words are "
                "matched individually rather than as one phrase. Inter-property transfers "
                "are excluded here and handled as Transfer funds below."
            ),
        )

    if transfer_position >= 0:
        return (
            "Transfer funds",
            (
                "Protection rule: explicit transfer rows stay inter-property transfers "
                "even when nearby PDF extraction also includes rent or management-fee text."
            ),
        )

    if entry.kind == "income" and "rent income" in text:
        return (
            "Rental Income",
            (
                "Protection rule: when the line itself is rent income, nearby "
                "mowing/transfer words from PDF extraction should not relabel it."
            ),
        )

    if entry.kind == "expense" and "management fees" in text:
        return (
            "Property management fees",
            (
                "Protection rule: management-fee lines stay management fees even "
                "when nearby PDF text mentions transfers."
            ),
        )

    if entry.category == "Property Reserve" or "property cash reserve" in text:
        return (
            "Property Reserve",
            (
                "Historical expert splits treat reserve balance movements as "
                "Property Cash Reserve equity, not ordinary expense/income."
            ),
        )

    if contains_any(text, "accounting fee", "accounting fees", "annual account fee"):
        return (
            "Accounting fees",
            "Historical expert splits label annual/accounting fees as accounting fees.",
        )

    if "owner contribution" in text:
        return (
            "Owner Contribution",
            "Historical expert splits label owner-provided funds as partner investment.",
        )

    if entry.kind == "expense" and "move out" in text:
        return (
            "Move Out Refund",
            "Historical expert splits treat move-out refunds separately from repairs or rent.",
        )

    if contains_any(text, "transfer to", "transfer from"):
        return (
            "Transfer funds",
            "Historical expert splits use the property-transfer equity account for inter-property transfers.",
        )

    if "leasing fee" in text:
        return (
            "Leasing Fee",
            "Historical expert splits label leasing fees under Commissions & fees:Leasing Fee.",
        )

    if contains_any(text, "lease renewal fee", "renewal fee"):
        return (
            "Leasing Fee",
            (
                "Lease renewal fees post with leasing fees (Commissions & fees:Leasing "
                "Fee) — the accountant's majority and most recent practice (QBO deposits "
                "226/2023 and 398/2024; the single 2022 Admin Fee posting was superseded)."
            ),
        )

    if "admin fee" in text:
        return (
            "Admin Fee",
            "Historical expert splits treat admin fees as the Admin Fee income account.",
        )

    if "management fees" in text:
        return (
            "Property management fees",
            "Historical expert splits use Property Management Fees for management-fee deductions.",
        )

    if contains_any(text, "hvac", "heating"):
        return (
            "HVAC",
            "Historical expert splits use the HVAC repair sub-account for heating/HVAC work.",
        )

    if is_plumbing_repair_context(text):
        return (
            "Plumbing",
            (
                "Protection rule: water heater/valve/pipe work is plumbing repair "
                "(Repairs & maintenance:Plumbing), not a Water & sewer utility bill. "
                "Aligned to the accountant's 2021-2024 practice (they never used a "
                "top-level Contract labor account)."
            ),
        )

    if is_water_damage_context(text):
        return (
            "Repairs & Maintenance",
            (
                "Protection rule: water-damage/remediation work is general repair & "
                "maintenance, not a utility bill or Contract labor."
            ),
        )

    if contains_any(text, "gas utility", "gas - vacant utility"):
        return (
            "Gas Utility",
            "Historical expert splits keep gas utility charges under Utilities:Gas.",
        )

    if contains_any(text, "electric utility", "electricity", "electric - vacant", "epb electric"):
        return (
            "Electricity",
            "Historical expert splits use Utilities:Electricity for electric utility bills.",
        )

    if is_water_utility_context(text):
        return (
            "Water & sewer",
            "Historical expert splits use Utilities:Water & sewer for water or sewer bills.",
        )

    if contains_any(text, "door installation", "door sweep", "threshold"):
        return (
            "Door Installation",
            "Historical expert splits use Door Installation for door/threshold work.",
        )

    if contains_any(text, "dishwasher installation", "install new dishwasher"):
        return (
            "Dishwasher Installation",
            "Historical expert splits separate dishwasher installation labor from the appliance purchase.",
        )

    if contains_any(text, "refrigerator diagnostic"):
        return (
            "Refrigerator maintenance",
            "Protection rule: refrigerator diagnostics are service/maintenance, not appliance purchases.",
        )

    if contains_any(text, "dishwasher diagnosis", "diagnosis"):
        return (
            "Repairs & Maintenance",
            "Protection rule: diagnosis/service calls are repairs and maintenance, not purchases.",
        )

    if contains_any(text, "dishwasher - payment", "appliance", "new fridge"):
        return (
            "Appliances",
            "Historical expert splits use Appliances for appliance purchases/payments.",
        )

    if contains_any(text, "refrigerator maintenance", "refrigerator repair"):
        return (
            "Refrigerator maintenance",
            "Historical expert splits use the refrigerator maintenance repair sub-account.",
        )

    if contains_any(text, "pest control", "exterminator"):
        return (
            "Exterminator",
            "Historical expert splits use Contract labor:Exterminator for pest-control work.",
        )

    if entry.kind == "expense" and contains_any(text, "lawn service", "mowing service"):
        return (
            "Mowing Service",
            "Historical expert splits use Contract labor:Moving/Mowing Services for lawn service.",
        )

    if contains_any(text, "noise investigation", "reduce noise"):
        return (
            "Repairs & Maintenance",
            "Protection rule: noise investigation/service work is repair/maintenance even if the text says cleaning a fan.",
        )

    if "cleaning" in text:
        return (
            "Cleaning",
            "Historical expert splits use General business expenses:Cleaning for cleaning work.",
        )

    if contains_any(text, "smoke detector", "co detector", "carbon monoxide detector", "air filter"):
        return (
            "Repairs & Maintenance",
            (
                "Safety-device / filter supply-and-install work is repairs & maintenance "
                "(accountant precedent: 'rekey locks, co detectors' -> Repairs & "
                "maintenance, QBO deposit 402, Dec 2024). Placed after the cleaning rule "
                "so cleaning-context lines keep their 2023 Cleaning treatment."
            ),
        )

    if contains_any(text, "plumbing", "drainage pipe"):
        return (
            "Plumbing",
            "Historical expert splits use the Plumbing repair sub-account for plumbing/drainage work.",
        )

    if contains_any(text, "liability insurance", "liability to landlord insurance"):
        return (
            "Liability insurance",
            "Historical expert splits use the liability insurance sub-account for landlord liability insurance.",
        )

    if "insurance services" in text:
        return (
            "Insurance",
            "Historical expert splits use Insurance for general insurance-service charges.",
        )

    return None, ""


def normalized_entry_text(entry: StatementEntry) -> str:
    return " ".join(
        str(part or "").strip().lower()
        for part in (
            entry.category,
            entry.name,
            entry.property_class,
            entry.description,
        )
        if str(part or "").strip()
    )


def contains_any(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)


def first_position(text: str, *needles: str) -> int:
    positions = [text.find(needle) for needle in needles if text.find(needle) >= 0]
    return min(positions) if positions else -1


def is_plumbing_repair_context(text: str) -> bool:
    return contains_any(
        text,
        "water heater",
        "valve",
        "union below",
        "relit",
        "drainage pipe",
        "plumbing",
    )


def is_tenant_security_deposit(text: str) -> bool:
    """A tenant's security deposit received (a liability), not income.

    Matches the words "security" and "deposit" individually because PDF text
    extraction interleaves "Security Deposit Transfer" with the date, reference,
    and running-balance tokens (e.g. "Security 09/25/2025 #124… Transfer Deposit
    Transfer"), which defeated the old contiguous-phrase match. Callers must
    exclude inter-property transfers first (see is_interproperty_transfer).
    """
    return "security" in text and "deposit" in text


def is_interproperty_transfer(text: str) -> bool:
    """Money moved between the owner's own properties (paired +/- lines).

    The accountant booked these to the property-transfer equity account, not to
    Security deposits, even when the row also said "Security Deposit Transfer"
    (QBO deposit 176, 2023). The tell is an explicit "transfer to"/"transfer
    from" another property.
    """
    return contains_any(text, "transfer to", "transfer from")


def is_water_damage_context(text: str) -> bool:
    return contains_any(
        text,
        "water damage",
        "dry up water",
        "remediation",
        "resealed",
    )


def is_water_utility_context(text: str) -> bool:
    if contains_any(text, "water & sewer", "water and sewer"):
        return True
    if (
        contains_any(text, "vacant utility", "final bill")
        and not contains_any(text, "water", "sewer", "waste resources")
    ):
        return False
    return contains_any(
        text,
        "water - vacant utility",
        "sewer - vacant utility",
        "vacant utility",
        "final bill",
        "water bill",
        "sewer bill",
        "utility bill",
        "water utility",
        "sewer utility",
        "water services",
        "sewer services",
    )


def build_duplicate_and_context_warnings(
    entries: tuple[StatementEntry, ...]
) -> list[dict]:
    warnings: list[dict] = []
    # Group by (property, amount). The same amount recurring across different
    # properties/units — identical rent on two units, one flat mowing fee on
    # three properties — is expected on a consolidated statement, and the PDF
    # reconciliation already guarantees the line total. The only case worth a
    # human check is the same amount repeating WITHIN one property under
    # DIFFERENT categories, which can mean one of the lines is mislabeled.
    by_property_amount: dict[tuple[str, str], list[tuple[int, StatementEntry]]] = defaultdict(list)
    for index, entry in enumerate(entries, start=1):
        by_property_amount[(entry.property_class, str(entry.signed_amount))].append(
            (index, entry)
        )

    for (property_class, amount), matches in sorted(by_property_amount.items()):
        categories = {entry.category for _index, entry in matches}
        if len(matches) > 1 and len(categories) > 1:
            warnings.append(
                {
                    "type": "duplicate_or_similar_amounts",
                    "amount": amount,
                    "property_class": property_class,
                    "reason": (
                        "The same amount appears more than once on this property under "
                        "different categories. Confirm from the PDF source text that each "
                        "line is labeled correctly (one may be mislabeled)."
                    ),
                    "lines": [
                        {
                            "line": index,
                            "category": entry.category,
                            "property_class": entry.property_class,
                            "description": compact(entry.description),
                        }
                        for index, entry in matches
                    ],
                }
            )

    for index, entry in enumerate(entries, start=1):
        text = normalized_entry_text(entry)
        hints = sorted(category_hints(text))
        if len(hints) >= 2 and looks_like_merged_pdf_text(text):
            warnings.append(
                {
                    "type": "mixed_pdf_context",
                    "line": index,
                    "amount": str(entry.signed_amount),
                    "category": entry.category,
                    "property_class": entry.property_class,
                    "detected_topics": hints,
                    "reason": (
                        "The PDF text for this line appears to include nearby "
                        "transactions. The label was protected by priority rules, "
                        "but this line should still be reviewed in audit output."
                    ),
                    "description": compact(entry.description),
                }
            )

    return warnings


def category_hints(text: str) -> set[str]:
    hints = set()
    if "rent income" in text:
        hints.add("rent income")
    if "transfer to" in text or "transfer from" in text:
        hints.add("transfer")
    if "management fees" in text:
        hints.add("management fees")
    if "mowing service" in text or "lawn service" in text:
        hints.add("mowing")
    if "water" in text or "sewer" in text:
        hints.add("water/sewer")
    if "admin fee" in text:
        hints.add("admin fee")
    if "move out refund" in text:
        hints.add("move out refund")
    if "owner contribution" in text:
        hints.add("owner contribution")
    if "property cash reserve" in text:
        hints.add("property reserve")
    return hints


def looks_like_merged_pdf_text(text: str) -> bool:
    date_count = len(re.findall(r"\b\d{2}/\d{2}/\d{4}\b", text))
    marker_count = sum(
        text.count(marker)
        for marker in (" receipt ", " check ", " echeck ", " transfer ")
    )
    return date_count >= 2 or marker_count >= 2


def compact(value: str, limit: int = 240) -> str:
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[: limit - 3] + "..."
