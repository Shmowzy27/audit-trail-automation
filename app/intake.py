"""Intake pipeline: get new owner packets in, and find unposted deposits.

Three read-mostly steps, one orchestrator (`run_intake`):

1. ``fetch_gmail_packets`` — search Gmail for new owner-statement PDFs (Nashville
   packets arrive by email), download them straight into the statements folder
   under the standardized name. Already-seen messages are skipped via the same
   ``runtime/state.json`` dedupe the ``gmail`` command uses.
2. ``ingest_folder`` — stage PDFs dropped into an intake folder (e.g. manual
   downloads from the Keyrenter AppFolio portal) into the statements folder,
   renamed to the standard. This is the manual-download half of the staged
   AppFolio plan; a browser fetcher can feed the same folder later.
3. ``scan_unposted_deposits`` — find recent QuickBooks deposits that are still
   UNSPLIT (a single line), pair each with its packet by amount + date window,
   and run the normal screening pipeline:
   - clean screening (``ready_to_split``, applies allowed, no warnings, no
     low-confidence lines) -> auto-apply, but ONLY with ``apply=True``;
   - anything flagged -> reported as queued for the review UI. Never bypassed.

Everything is dry-run by default, like every other command in this project.
"""

from __future__ import annotations

import os
import re
import tempfile
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from .gmail_client import GmailClient, quiet_profile
from .parser import parse_statement_pdf
from .quickbooks import QuickBooksClient, QuickBooksError, deposit_total
from .rename import infer_provider, packet_name
from .service import process_pdf
from .state import ProcessingState

# Standardized packet filename -> (provider, month, year, net amount) without
# opening the PDF. Keeps deposit pairing cheap across a large folder.
PACKET_FILE_RE = re.compile(
    r"^Owner Packet - (?P<provider>Keyrenter|Nashville) - "
    r"(?P<month>\d{2})-(?P<year>\d{2}) - (?P<amount>-?[\d.]+)\.pdf$",
    re.IGNORECASE,
)

PROVIDER_SUBFOLDERS = {
    "Keyrenter": "keyrenter history",
    "Nashville": "nashville history",
}


def provider_folder(statements_folder: Path, provider: str) -> Path:
    return statements_folder / PROVIDER_SUBFOLDERS.get(provider, provider.lower())


def stage_pdf(content_path: Path, statements_folder: Path, *, apply: bool) -> dict:
    """Parse one PDF and plan/perform its move into the statements folder."""
    try:
        statement = parse_statement_pdf(content_path)
    except Exception as exc:  # noqa: BLE001 - non-statement PDFs are reported, not fatal.
        return {"file": content_path.name, "action": "not_a_statement", "detail": str(exc)[:140]}

    provider = infer_provider("", statement.property_name)
    name = packet_name(provider, statement)
    target = provider_folder(statements_folder, provider) / name
    if target.exists():
        return {"file": content_path.name, "action": "duplicate", "target": str(target)}
    result = {"file": content_path.name, "action": "staged" if apply else "would_stage", "target": str(target)}
    if apply:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content_path.read_bytes())
    return result


def ingest_folder(intake_folder: str | Path, statements_folder: str | Path, *, apply: bool = False) -> dict:
    """Stage every PDF in the intake folder into the statements folder."""
    intake = Path(intake_folder)
    statements = Path(statements_folder)
    if not intake.is_dir():
        return {"step": "ingest-folder", "skipped": f"No intake folder at {intake}."}
    items = []
    for pdf in sorted(intake.glob("*.pdf")):
        item = stage_pdf(pdf, statements, apply=apply)
        if apply and item["action"] == "staged":
            pdf.unlink()  # staged copies live in the statements folder now
        items.append(item)
    return {"step": "ingest-folder", "folder": str(intake), "applied": apply, "items": items}


def _stored_targets_missing(state: ProcessingState, message_id: str) -> bool:
    """True when a packet this message previously produced is GONE from the folder.

    Makes the Gmail dedupe self-healing: delete a staged packet and the next
    fetch re-processes its email and restores it, instead of the processed-
    message memory silently refusing to re-download forever.
    """
    record = state.data.get("processed_messages", {}).get(message_id, {})
    attachments = record.get("result", {}).get("attachments", [])
    targets = [
        a.get("target")
        for a in attachments
        if a.get("action") in ("staged", "duplicate") and a.get("target")
    ]
    return any(not Path(t).exists() for t in targets)


def fetch_gmail_packets(
    config: dict,
    statements_folder: str | Path,
    *,
    max_results: int = 25,
    apply: bool = False,
) -> dict:
    """Download new owner-statement attachments from Gmail into the statements folder."""
    statements = Path(statements_folder)
    token_file = os.getenv("GOOGLE_TOKEN_FILE", "secrets/google_token.json")
    # Fail FAST if not connected. Constructing GmailClient with a missing/broken
    # token would launch an interactive consent flow — deadly inside a
    # background job. quiet_profile never does that.
    connected_as = quiet_profile(token_file)
    if not connected_as:
        raise RuntimeError("Gmail is not connected — open Connectors and connect it first.")
    gmail = GmailClient(os.environ["GOOGLE_CREDENTIALS_FILE"], token_file)
    state = ProcessingState("runtime/state.json")
    messages = []
    for message in gmail.search(config["gmail_query"], max_results=max_results):
        message_id = message["id"]
        if state.is_processed(message_id) and not _stored_targets_missing(state, message_id):
            continue
        attachments = []
        with tempfile.TemporaryDirectory(prefix="owner-intake-") as temp_dir:
            for filename, content in gmail.pdf_attachments(message_id):
                pdf_path = Path(temp_dir) / Path(filename).name
                pdf_path.write_bytes(content)
                attachments.append(stage_pdf(pdf_path, statements, apply=apply))
        entry = {"message_id": message_id, "attachments": attachments}
        messages.append(entry)
        # A message is done when every attachment was handled — newly staged OR
        # already in the folder (duplicate). Marking duplicates too stops the same
        # old emails being re-downloaded on every run. Messages with unparseable
        # PDFs stay unmarked so a future parser improvement can pick them up.
        handled = bool(attachments) and all(
            a["action"] in ("staged", "duplicate") for a in attachments
        )
        if apply and handled:
            state.mark_processed(message_id, entry)
    return {
        "step": "gmail",
        "connected_as": connected_as,
        "query": config.get("gmail_query", ""),
        "applied": apply,
        "new_messages": len(messages),
        "messages": messages,
    }


def packets_by_amount(statements_folder: str | Path) -> dict[Decimal, list[dict]]:
    """Index standardized packets by their net amount (from the filename)."""
    index: dict[Decimal, list[dict]] = {}
    for pdf in Path(statements_folder).rglob("*.pdf"):
        match = PACKET_FILE_RE.match(pdf.name)
        if not match:
            continue
        amount = Decimal(match.group("amount"))
        index.setdefault(amount, []).append(
            {
                "path": pdf,
                "provider": match.group("provider"),
                "month": int(match.group("month")),
                "year": 2000 + int(match.group("year")),
            }
        )
    return index


def deposit_in_window(packet: dict, txn_date: str, *, days_before: int = 5, days_after: int = 75) -> bool:
    """Same date window the automation's deposit search uses."""
    try:
        deposited = date.fromisoformat(txn_date)
    except (TypeError, ValueError):
        return False
    month_start = date(packet["year"], packet["month"], 1)
    month_end = month_start.replace(day=monthrange(packet["year"], packet["month"])[1])
    return month_start - timedelta(days=days_before) <= deposited <= month_end + timedelta(days=days_after)


def should_auto_apply(dry_result: dict) -> bool:
    """Auto-apply policy: only a completely clean screening qualifies.

    Clean = ready_to_split, apply allowed (which requires zero warnings), and no
    low-confidence lines. Everything else goes to the human review UI — the
    screening gate is never bypassed.
    """
    screening = dry_result.get("screening", {})
    confidence = dry_result.get("expert_rule_review", {}).get("confidence", {})
    return (
        screening.get("status") == "ready_to_split"
        and screening.get("apply_allowed") is True
        and not dry_result.get("expert_rule_review", {}).get("warnings")
        and (confidence.get("summary", {}).get("low", 1) == 0)
    )


def scan_unposted_deposits(
    config: dict,
    statements_folder: str | Path,
    *,
    days: int = 120,
    apply: bool = False,
) -> dict:
    """Find recent unsplit QuickBooks deposits and run the pipeline on their packets."""
    quickbooks = QuickBooksClient()
    end = date.today()
    start = end - timedelta(days=days)
    index = packets_by_amount(statements_folder)

    results = []
    for deposit in quickbooks.deposits_between(start, end):
        lines = [l for l in deposit.get("Line", []) if l.get("DetailType") == "DepositLineDetail"]
        if len(lines) > 1:
            continue  # already split
        total = deposit_total(deposit)
        entry = {
            "deposit_id": deposit.get("Id"),
            "date": deposit.get("TxnDate"),
            "total": str(total),
            "memo": deposit.get("PrivateNote", ""),
        }
        candidates = [
            p for p in index.get(total, [])
            if deposit_in_window(p, deposit.get("TxnDate", ""))
        ]
        if not candidates:
            entry["action"] = "no_packet"
        elif len(candidates) > 1:
            entry["action"] = "ambiguous"
            entry["packets"] = [str(p["path"]) for p in candidates]
        else:
            pdf = candidates[0]["path"]
            entry["packet"] = str(pdf)
            try:
                dry = process_pdf(pdf, config, apply=False, deposit_id=entry["deposit_id"])
            except (QuickBooksError, ValueError) as exc:
                entry["action"] = "error"
                entry["detail"] = str(exc)[:200]
                results.append(entry)
                continue
            screening = dry.get("screening", {})
            entry["screening_status"] = screening.get("status")
            entry["warnings"] = len(dry.get("expert_rule_review", {}).get("warnings", []))
            if should_auto_apply(dry):
                if apply:
                    outcome = process_pdf(pdf, config, apply=True, deposit_id=entry["deposit_id"])
                    entry["action"] = "auto_applied"
                    entry["result"] = outcome.get("result")
                else:
                    entry["action"] = "would_auto_apply"
            else:
                entry["action"] = "queued_for_review"
                entry["reasons"] = screening.get("reasons", [])
        results.append(entry)

    summary = {}
    for r in results:
        summary[r["action"]] = summary.get(r["action"], 0) + 1
    return {
        "step": "scan-deposits",
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "applied": apply,
        "unsplit_deposit_count": len(results),
        "summary": summary,
        "deposits": results,
    }


def run_intake(
    config: dict,
    *,
    statements_folder: str | Path,
    intake_folder: str | Path | None = None,
    skip_gmail: bool = False,
    skip_scan: bool = False,
    days: int = 120,
    max_results: int = 25,
    apply: bool = False,
) -> dict:
    """One command: fetch new packets, stage dropped ones, scan for unposted deposits."""
    report: dict = {"mode": "intake", "applied": apply, "steps": []}
    if not skip_gmail:
        try:
            report["steps"].append(
                fetch_gmail_packets(config, statements_folder, max_results=max_results, apply=apply)
            )
        except KeyError as exc:
            report["steps"].append({"step": "gmail", "skipped": f"Missing environment variable: {exc}."})
    if intake_folder:
        report["steps"].append(ingest_folder(intake_folder, statements_folder, apply=apply))
    if not skip_scan:
        report["steps"].append(
            scan_unposted_deposits(config, statements_folder, days=days, apply=apply)
        )
    return report
