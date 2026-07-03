from __future__ import annotations

import json
import os
import re
import time
from base64 import b64encode
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable

import requests

from .models import OwnerStatement


class QuickBooksError(RuntimeError):
    pass


class QuickBooksClient:
    def __init__(self):
        self.client_id = required_env("QBO_CLIENT_ID")
        self.client_secret = required_env("QBO_CLIENT_SECRET")
        self.environment = os.getenv("QBO_ENVIRONMENT", "sandbox").lower()
        self.token_file = Path(os.getenv("QBO_TOKEN_FILE", "secrets/qbo_token.json"))
        self.minor_version = os.getenv("QBO_MINOR_VERSION", "").strip()
        self.base_url = (
            "https://sandbox-quickbooks.api.intuit.com"
            if self.environment == "sandbox"
            else "https://quickbooks.api.intuit.com"
        )
        if not self.token_file.exists():
            raise QuickBooksError(
                f"QuickBooks token file not found: {self.token_file}. "
                "Run authorize_quickbooks.py first."
            )
        self.tokens = json.loads(self.token_file.read_text(encoding="utf-8"))
        self.realm_id = self.tokens["realm_id"]

    def _save_tokens(self) -> None:
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.token_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.tokens, indent=2), encoding="utf-8")
        temporary.replace(self.token_file)

    def _access_token(self, force_refresh: bool = False) -> str:
        expires_at = float(self.tokens.get("expires_at", 0))
        if not force_refresh and expires_at > time.time() + 90:
            return self.tokens["access_token"]

        basic = b64encode(
            f"{self.client_id}:{self.client_secret}".encode("utf-8")
        ).decode("ascii")
        response = requests.post(
            "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
            headers={
                "Authorization": f"Basic {basic}",
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.tokens["refresh_token"],
            },
            timeout=30,
        )
        if not response.ok:
            raise QuickBooksError(
                f"QuickBooks token refresh failed ({response.status_code}): "
                f"{response.text}"
            )
        refreshed = response.json()
        self.tokens.update(refreshed)
        self.tokens["expires_at"] = time.time() + int(refreshed["expires_in"])
        self._save_tokens()
        return self.tokens["access_token"]

    def request(
        self, method: str, path: str, *, params: dict | None = None, json_body=None
    ) -> dict:
        request_params = dict(params or {})
        if self.minor_version:
            request_params["minorversion"] = self.minor_version

        for attempt in range(2):
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                params=request_params,
                json=json_body,
                headers={
                    "Authorization": f"Bearer {self._access_token(attempt == 1)}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=45,
            )
            if response.status_code != 401 or attempt == 1:
                break
        if not response.ok:
            raise QuickBooksError(
                f"QuickBooks API failed ({response.status_code}): {response.text}"
            )
        return response.json()

    def query(self, query: str) -> dict:
        return self.request(
            "GET",
            f"/v3/company/{self.realm_id}/query",
            params={"query": query},
        ).get("QueryResponse", {})

    def get_deposit(self, deposit_id: str) -> dict:
        response = self.request(
            "GET", f"/v3/company/{self.realm_id}/deposit/{deposit_id}"
        )
        return response["Deposit"]

    def deposits_between(self, start: date, end: date) -> list[dict]:
        query = (
            "SELECT * FROM Deposit "
            f"WHERE TxnDate >= '{start.isoformat()}' "
            f"AND TxnDate <= '{end.isoformat()}' "
            "STARTPOSITION 1 MAXRESULTS 1000"
        )
        return self.query(query).get("Deposit", [])

    def all_accounts(self) -> list[dict]:
        return self.query(
            "SELECT * FROM Account STARTPOSITION 1 MAXRESULTS 1000"
        ).get("Account", [])

    def all_customers(self) -> list[dict]:
        return self.query(
            "SELECT * FROM Customer STARTPOSITION 1 MAXRESULTS 1000"
        ).get("Customer", [])

    def resolve_account(self, account_name: str, accounts: Iterable[dict]) -> dict:
        target = normalize_name(account_name)
        matches = [
            account
            for account in accounts
            if target
            in {
                normalize_name(account.get("FullyQualifiedName", "")),
                normalize_name(account.get("Name", "")),
            }
        ]
        if len(matches) != 1:
            raise QuickBooksError(
                f"Expected one QuickBooks account named '{account_name}', "
                f"but found {len(matches)}."
            )
        return matches[0]

    def resolve_customer(self, customer_name: str, customers: Iterable[dict]) -> dict:
        target = normalize_name(customer_name)
        matches = [
            customer
            for customer in customers
            if target
            in {
                normalize_name(customer.get("DisplayName", "")),
                normalize_name(customer.get("FullyQualifiedName", "")),
            }
        ]
        if len(matches) != 1:
            raise QuickBooksError(
                f"Expected one QuickBooks customer named '{customer_name}', "
                f"but found {len(matches)}."
            )
        return matches[0]

    def find_matching_deposit(
        self,
        statement: OwnerStatement,
        property_settings: dict,
        search_settings: dict,
    ) -> dict:
        month_start = statement.statement_month
        month_end = month_start.replace(day=monthrange(month_start.year, month_start.month)[1])
        start = month_start - timedelta(
            days=int(search_settings.get("days_before_statement", 5))
        )
        end = month_end + timedelta(
            days=int(search_settings.get("days_after_month_end", 75))
        )
        amount = statement.stated_net_income
        candidates = [
            deposit
            for deposit in self.deposits_between(start, end)
            if abs(deposit_total(deposit) - amount) <= Decimal("0.01")
        ]
        if not candidates:
            raise QuickBooksError(
                f"No deposit for {amount} was found between {start} and {end}."
            )
        if len(candidates) == 1:
            return candidates[0]

        keywords = [
            statement.property_name,
            *property_settings.get("memo_keywords", []),
        ]
        scored = [
            (
                sum(
                    1
                    for keyword in keywords
                    if normalize_name(keyword) in normalize_name(json.dumps(deposit))
                ),
                deposit,
            )
            for deposit in candidates
        ]
        best_score = max(score for score, _deposit in scored)
        best = [deposit for score, deposit in scored if score == best_score]
        if best_score > 0 and len(best) == 1:
            return best[0]
        ids = ", ".join(str(item.get("Id")) for item in candidates)
        raise QuickBooksError(
            f"Multiple deposits match {amount}; pass --deposit-id. Candidate IDs: {ids}"
        )

    def create_split_plan(
        self,
        statement: OwnerStatement,
        property_settings: dict,
        deposit: dict,
        *,
        allow_resplit: bool = False,
        overrides: dict | None = None,
    ) -> dict:
        if abs(deposit_total(deposit) - statement.stated_net_income) > Decimal("0.01"):
            raise QuickBooksError(
                f"Deposit {deposit.get('Id')} total does not equal statement net income."
            )

        existing_lines = [
            line
            for line in deposit.get("Line", [])
            if line.get("DetailType") == "DepositLineDetail"
        ]
        unsupported_lines = [
            line
            for line in deposit.get("Line", [])
            if line.get("DetailType") != "DepositLineDetail"
            or line.get("LinkedTxn")
        ]
        if unsupported_lines:
            raise QuickBooksError(
                f"Deposit {deposit.get('Id')} contains linked or non-editable lines. "
                "It was not changed."
            )
        if len(existing_lines) > 1 and not allow_resplit:
            return {
                "status": "already_split",
                "deposit_id": deposit["Id"],
                "existing_line_count": len(existing_lines),
                "deposit_total": str(deposit_total(deposit)),
            }
        if len(existing_lines) != 1 and not allow_resplit:
            raise QuickBooksError(
                f"Deposit {deposit.get('Id')} has {len(existing_lines)} editable "
                "deposit lines; expected exactly one unsplit line."
            )

        category_accounts = property_settings["category_accounts"]
        # Per-line reviewer overrides {line_num: {"account": FQN, "customer": name}}.
        # Amounts are never overridable — that protects reconciliation. Overridden
        # accounts/customers are resolved against QBO, so an invalid choice fails here.
        line_overrides = {int(k): v for k, v in (overrides or {}).items()}
        # A line only counts as unmapped if config has no account AND no override
        # supplies one, so a reviewer can unblock a line by assigning an account.
        missing_categories = sorted(
            {
                entry.category
                for index, entry in enumerate(statement.entries, start=1)
                if entry.category not in category_accounts
                and not line_overrides.get(index, {}).get("account")
            }
        )
        if missing_categories:
            raise QuickBooksError(
                "No QuickBooks account mapping for: " + ", ".join(missing_categories)
            )

        accounts = self.all_accounts()
        customers = self.all_customers()
        default_customer = self.resolve_customer(
            property_settings["quickbooks_customer"], customers
        )
        customer_by_property_class = {}
        for property_class, customer_name in property_settings.get(
            "customer_by_property_class", {}
        ).items():
            customer_by_property_class[property_class] = self.resolve_customer(
                customer_name, customers
            )
        # Resolve every configured account, collecting ALL failures so a fresh
        # company's missing accounts surface together (not one per re-run).
        account_refs = {}
        missing_accounts = []
        for category, account_name in category_accounts.items():
            try:
                account_refs[category] = self.resolve_account(account_name, accounts)
            except QuickBooksError as exc:
                missing_accounts.append(f"{account_name} (for {category}): {exc}")
        if missing_accounts:
            raise QuickBooksError(
                "QuickBooks accounts from config could not be resolved — create them or "
                "fix the mapping:\n  " + "\n  ".join(sorted(missing_accounts))
            )
        description_mode = property_settings.get("description_mode", "full")
        blank_entity_categories = set(
            property_settings.get("blank_entity_categories", [])
        )

        lines = []
        for index, entry in enumerate(statement.entries, start=1):
            override = line_overrides.get(index, {})
            account = (
                self.resolve_account(override["account"], accounts)
                if override.get("account")
                else account_refs[entry.category]
            )
            customer = customer_by_property_class.get(
                entry.property_class, default_customer
            )
            if override.get("customer"):
                customer = self.resolve_customer(override["customer"], customers)
            description_parts = [
                part
                for part in (
                    entry.name,
                    entry.description,
                    entry.transaction_date.isoformat() if entry.transaction_date else "",
                )
                if part
            ]
            deposit_line_detail = {
                "AccountRef": {
                    "value": account["Id"],
                    "name": account.get(
                        "FullyQualifiedName", account.get("Name", "")
                    ),
                }
            }
            if entry.category not in blank_entity_categories or override.get("customer"):
                deposit_line_detail["Entity"] = {
                    "value": customer["Id"],
                    "name": customer.get("DisplayName", ""),
                    "type": "Customer",
                }
            line = {
                "LineNum": index,
                "Amount": float(entry.signed_amount),
                "DetailType": "DepositLineDetail",
                "DepositLineDetail": deposit_line_detail,
            }
            if description_mode != "blank":
                line["Description"] = " - ".join(description_parts)[:4000]
            lines.append(line)

        # QBO deposit full-updates do NOT delete existing lines that are simply
        # omitted from the payload (they get kept, so a naive replace appends).
        # Reuse each existing line's Id on the new lines so QBO updates those in
        # place; remaining new lines are added. This fully replaces the original
        # line(s) for the unsplit case. (If the deposit had MORE existing lines
        # than the new split, the surplus can't be deleted this way - the apply
        # verification gate will catch that mismatch instead of corrupting data.)
        for new_line, existing in zip(lines, existing_lines):
            existing_id = existing.get("Id")
            if existing_id is not None:
                new_line["Id"] = existing_id

        planned_total = sum(
            (Decimal(str(line["Amount"])) for line in lines), Decimal("0")
        )
        if abs(planned_total - statement.stated_net_income) > Decimal("0.01"):
            raise QuickBooksError(
                f"Refusing to update: split total {planned_total} does not equal "
                f"deposit total {statement.stated_net_income}."
            )

        payload = {
            "Id": deposit["Id"],
            "SyncToken": deposit["SyncToken"],
            "sparse": False,
            "TxnDate": deposit["TxnDate"],
            "DepositToAccountRef": deposit["DepositToAccountRef"],
            "Line": lines,
        }
        for optional in (
            "PrivateNote",
            "CurrencyRef",
            "ExchangeRate",
            "DepartmentRef",
            "TxnSource",
            "CashBack",
        ):
            if optional in deposit:
                payload[optional] = deposit[optional]

        return {
            "status": "ready",
            "deposit_id": deposit["Id"],
            "deposit_total": str(deposit_total(deposit)),
            "split_line_count": len(lines),
            "replacing_existing_line_count": len(existing_lines)
            if allow_resplit
            else 1,
            "original_deposit": deposit,
            "update_payload": payload,
        }

    def apply_split_plan(self, plan: dict, audit_dir: str | Path) -> dict:
        if plan["status"] != "ready":
            return plan

        audit_path = Path(audit_dir)
        audit_path.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = audit_path / f"deposit-{plan['deposit_id']}-{stamp}.json"
        backup.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        response = self.request(
            "POST",
            f"/v3/company/{self.realm_id}/deposit",
            json_body=plan["update_payload"],
        )
        updated = response["Deposit"]

        # Post-write verification: prove the deposit matches the plan before ever
        # reporting success. Safety: never silently modify the books (a full-update
        # that appends instead of replacing must fail loudly, not look "updated").
        actual_lines = [
            line
            for line in updated.get("Line", [])
            if line.get("DetailType") == "DepositLineDetail"
        ]
        actual_total = deposit_total(updated)
        expected_total = Decimal(plan["deposit_total"])
        expected_count = plan["split_line_count"]
        if (
            abs(actual_total - expected_total) > Decimal("0.01")
            or len(actual_lines) != expected_count
        ):
            failure = audit_path / f"deposit-{plan['deposit_id']}-{stamp}-MISMATCH.json"
            failure.write_text(json.dumps(updated, indent=2), encoding="utf-8")
            raise QuickBooksError(
                f"Apply verification FAILED for deposit {updated['Id']}: expected "
                f"{expected_count} lines totaling {expected_total}, but QuickBooks now "
                f"has {len(actual_lines)} lines totaling {actual_total}. The deposit may "
                f"be in a bad state - review it. Backup: {backup}; response: {failure}."
            )

        return {
            "status": "updated",
            "deposit_id": updated["Id"],
            "deposit_total": str(deposit_total(updated)),
            "split_line_count": len(actual_lines),
            "audit_file": str(backup),
        }


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise QuickBooksError(f"Missing required environment variable: {name}")
    return value


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def deposit_total(deposit: dict) -> Decimal:
    if "TotalAmt" in deposit:
        return Decimal(str(deposit["TotalAmt"]))
    return sum(
        (
            Decimal(str(line.get("Amount", 0)))
            for line in deposit.get("Line", [])
            if line.get("DetailType") == "DepositLineDetail"
        ),
        Decimal("0"),
    )
