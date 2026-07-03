from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

from .quickbooks import QuickBooksClient, deposit_total, normalize_name


def read_accounts(*, keyword: str | None = None, include_raw: bool = False) -> dict:
    quickbooks = QuickBooksClient()
    accounts = quickbooks.all_accounts()

    if keyword:
        target_keyword = normalize_name(keyword)
        accounts = [
            account
            for account in accounts
            if target_keyword
            in normalize_name(
                " ".join(
                    str(account.get(field, ""))
                    for field in (
                        "Name",
                        "FullyQualifiedName",
                        "AccountType",
                        "AccountSubType",
                    )
                )
            )
        ]

    accounts.sort(
        key=lambda account: (
            str(account.get("FullyQualifiedName") or account.get("Name") or ""),
            str(account.get("Id") or ""),
        )
    )
    return {
        "mode": "qbo-read-only",
        "status": "ready",
        "keyword": keyword,
        "account_count": len(accounts),
        "accounts": [
            summarize_account(account, include_raw=include_raw) for account in accounts
        ],
    }


def read_customers(*, keyword: str | None = None, include_raw: bool = False) -> dict:
    quickbooks = QuickBooksClient()
    customers = quickbooks.all_customers()

    if keyword:
        target_keyword = normalize_name(keyword)
        customers = [
            customer
            for customer in customers
            if target_keyword
            in normalize_name(
                " ".join(
                    str(customer.get(field, ""))
                    for field in (
                        "DisplayName",
                        "FullyQualifiedName",
                        "CompanyName",
                        "GivenName",
                        "FamilyName",
                    )
                )
            )
        ]

    customers.sort(
        key=lambda customer: (
            str(customer.get("FullyQualifiedName") or customer.get("DisplayName") or ""),
            str(customer.get("Id") or ""),
        )
    )
    return {
        "mode": "qbo-read-only",
        "status": "ready",
        "keyword": keyword,
        "customer_count": len(customers),
        "customers": [
            summarize_customer(customer, include_raw=include_raw)
            for customer in customers
        ],
    }


def read_deposit(deposit_id: str, *, include_raw: bool = False) -> dict:
    quickbooks = QuickBooksClient()
    deposit = quickbooks.get_deposit(deposit_id)
    return {
        "mode": "qbo-read-only",
        "status": "ready",
        "deposit": summarize_deposit(deposit, include_raw=include_raw),
    }


def read_deposits(
    start: str,
    end: str,
    *,
    amount: str | None = None,
    memo_keyword: str | None = None,
    include_raw: bool = False,
) -> dict:
    quickbooks = QuickBooksClient()
    deposits = quickbooks.deposits_between(parse_date(start), parse_date(end))

    if amount:
        target_amount = Decimal(amount)
        deposits = [
            deposit
            for deposit in deposits
            if abs(deposit_total(deposit) - target_amount) <= Decimal("0.01")
        ]

    if memo_keyword:
        target_keyword = normalize_name(memo_keyword)
        deposits = [
            deposit
            for deposit in deposits
            if target_keyword in normalize_name(json.dumps(deposit))
        ]

    deposits.sort(key=lambda item: (item.get("TxnDate", ""), str(item.get("Id", ""))))

    return {
        "mode": "qbo-read-only",
        "status": "ready",
        "start": start,
        "end": end,
        "filters": {
            "amount": amount,
            "memo_keyword": memo_keyword,
        },
        "deposit_count": len(deposits),
        "deposits": [
            summarize_deposit(deposit, include_raw=include_raw)
            for deposit in deposits
        ],
    }


def summarize_deposit(deposit: dict[str, Any], *, include_raw: bool = False) -> dict:
    split_lines = [
        summarize_deposit_line(line)
        for line in deposit.get("Line", [])
        if line.get("DetailType") == "DepositLineDetail"
    ]
    result = {
        "id": deposit.get("Id"),
        "sync_token": deposit.get("SyncToken"),
        "date": deposit.get("TxnDate"),
        "total": str(deposit_total(deposit)),
        "memo": deposit.get("PrivateNote", ""),
        "deposit_to_account": ref_name(deposit.get("DepositToAccountRef", {})),
        "line_count": len(split_lines),
        "lines": split_lines,
    }
    if include_raw:
        result["raw_deposit"] = deposit
    return result


def summarize_account(account: dict[str, Any], *, include_raw: bool = False) -> dict:
    result = {
        "id": account.get("Id"),
        "name": account.get("Name", ""),
        "fully_qualified_name": account.get("FullyQualifiedName", ""),
        "account_type": account.get("AccountType", ""),
        "account_sub_type": account.get("AccountSubType", ""),
        "active": account.get("Active", ""),
    }
    if include_raw:
        result["raw_account"] = account
    return result


def summarize_customer(customer: dict[str, Any], *, include_raw: bool = False) -> dict:
    result = {
        "id": customer.get("Id"),
        "display_name": customer.get("DisplayName", ""),
        "fully_qualified_name": customer.get("FullyQualifiedName", ""),
        "company_name": customer.get("CompanyName", ""),
        "active": customer.get("Active", ""),
    }
    if include_raw:
        result["raw_customer"] = customer
    return result


def summarize_deposit_line(line: dict[str, Any]) -> dict:
    detail = line.get("DepositLineDetail", {})
    return {
        "line_num": line.get("LineNum"),
        "amount": str(Decimal(str(line.get("Amount", 0)))),
        "received_from": ref_name(detail.get("Entity", {})),
        "account": ref_name(detail.get("AccountRef", {})),
        "description": line.get("Description", ""),
        "payment_method": ref_name(detail.get("PaymentMethodRef", {})),
        "ref_no": detail.get("CheckNum", ""),
        "linked_txn": line.get("LinkedTxn", []),
    }


def ref_name(ref: dict[str, Any]) -> str:
    return str(ref.get("name") or ref.get("Name") or ref.get("value") or "")


def parse_date(value: str) -> date:
    return date.fromisoformat(value)
