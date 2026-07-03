from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .config import property_config
from .approvals import verify_screening_approval
from .expert_rules import apply_expert_history_rules
from .gmail_client import GmailClient
from .parser import parse_statement_pdf
from .quickbooks import QuickBooksClient, QuickBooksError
from .screening import build_screening_report, build_unmapped_preview
from .state import ProcessingState


def process_pdf(
    pdf_path: str | Path,
    config: dict,
    *,
    apply: bool,
    deposit_id: str | None = None,
    allow_resplit: bool = False,
    approval_file: str | Path | None = None,
    overrides: dict | None = None,
) -> dict:
    statement = parse_statement_pdf(pdf_path)
    settings = property_config(config, statement.property_name)
    statement, expert_rule_review = apply_expert_history_rules(statement, settings)
    quickbooks = QuickBooksClient()
    deposit = (
        quickbooks.get_deposit(deposit_id)
        if deposit_id
        else quickbooks.find_matching_deposit(
            statement, settings, config.get("deposit_search", {})
        )
    )
    # A line is "unmapped" only if its category has no account AND the reviewer
    # hasn't assigned one via an override. Assigning an account in the UI clears
    # the block for that line, so the split can build and go through review.
    category_accounts = settings.get("category_accounts", {})
    line_overrides = {int(k): v for k, v in (overrides or {}).items()}
    missing_categories = sorted(
        {
            entry.category
            for index, entry in enumerate(statement.entries, start=1)
            if entry.category not in category_accounts
            and not line_overrides.get(index, {}).get("account")
        }
    )

    screening_plan = {"status": "blocked"}
    planning_error = None
    unmapped_preview = None
    if not missing_categories:
        try:
            screening_plan = quickbooks.create_split_plan(
                statement, settings, deposit, allow_resplit=True, overrides=overrides
            )
        except QuickBooksError as exc:
            planning_error = str(exc)
    else:
        unmapped_preview = build_unmapped_preview(statement, settings, overrides)

    screening = build_screening_report(
        statement=statement,
        expert_rule_review=expert_rule_review,
        deposit=deposit,
        screening_plan=screening_plan,
        missing_categories=missing_categories,
        planning_error=planning_error,
        unmapped_preview=unmapped_preview,
    )

    if not apply:
        return {
            "mode": "dry-run",
            "statement": statement.to_dict(),
            "expert_rule_review": expert_rule_review,
            "screening": screening,
            "plan": screening_plan,
        }

    approval = (
        verify_screening_approval(
            screening,
            approval_file,
            allow_resplit=allow_resplit,
        )
        if not screening["apply_allowed"]
        else None
    )

    if not screening["apply_allowed"] and not (
        approval and approval.get("approved")
    ):
        return {
            "mode": "blocked-by-screening",
            "statement": statement.to_dict(),
            "expert_rule_review": expert_rule_review,
            "screening": screening,
            "approval": approval,
            "plan": screening_plan,
            "result": {
                "status": "not_updated",
                "reason": (
                    "Screening did not allow apply. QuickBooks was not changed."
                ),
            },
        }

    if screening["status"] == "already_split_matches":
        return {
            "mode": "screened-no-change",
            "statement": statement.to_dict(),
            "expert_rule_review": expert_rule_review,
            "screening": screening,
            "plan": screening_plan,
            "result": {
                "status": "already_split_matches",
                "deposit_id": screening.get("deposit_id"),
                "reason": "QuickBooks already matches the screened PDF split.",
            },
        }

    plan = quickbooks.create_split_plan(
        statement, settings, deposit, allow_resplit=allow_resplit, overrides=overrides
    )
    result = quickbooks.apply_split_plan(plan, "runtime/audit")
    return {
        "mode": "apply",
        "statement": statement.to_dict(),
        "expert_rule_review": expert_rule_review,
        "screening": screening,
        "approval": approval,
        "plan": plan,
        "result": result,
    }


def process_gmail(config: dict, *, apply: bool, max_results: int = 25) -> list[dict]:
    gmail = GmailClient(
        os.environ["GOOGLE_CREDENTIALS_FILE"],
        os.getenv("GOOGLE_TOKEN_FILE", "secrets/google_token.json"),
    )
    state = ProcessingState("runtime/state.json")
    results = []

    for message in gmail.search(config["gmail_query"], max_results=max_results):
        message_id = message["id"]
        if state.is_processed(message_id):
            continue

        message_results = []
        with tempfile.TemporaryDirectory(prefix="owner-statement-") as temp_dir:
            for filename, content in gmail.pdf_attachments(message_id):
                pdf_path = Path(temp_dir) / Path(filename).name
                pdf_path.write_bytes(content)
                try:
                    message_results.append(
                        {
                            "filename": filename,
                            "result": process_pdf(pdf_path, config, apply=apply),
                        }
                    )
                except ValueError as exc:
                    # Ignore unrelated PDFs attached to the same email.
                    message_results.append(
                        {"filename": filename, "status": "ignored", "reason": str(exc)}
                    )

        result = {"message_id": message_id, "attachments": message_results}
        results.append(result)
        successful = any(
            item.get("result", {}).get("plan", {}).get("status")
            in {"ready", "already_split"}
            or item.get("result", {}).get("result", {}).get("status")
            in {"updated", "already_split", "already_split_matches"}
            for item in message_results
        )
        if apply and successful:
            state.mark_processed(message_id, result)

    return results
