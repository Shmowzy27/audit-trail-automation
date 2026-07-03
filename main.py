from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from app.approvals import create_screening_approval
from app.config import load_config
from app.history import audit_statement_history, scan_statement_folder
from app.history_verify import verify_history_from_file
from app.intake import run_intake
from app.rename import rename_packets
from app.parser import parse_statement_pdf
from app.qbo_inspect import read_accounts, read_customers, read_deposit, read_deposits
from app.service import process_gmail, process_pdf
from app.split_audit import audit_split


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Split QuickBooks Online deposits from owner-statement PDFs."
    )
    parser.add_argument("--config", default="config.json")
    parser.add_argument(
        "--output",
        help="Also save the complete JSON result to this file for review.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    parse_command = subparsers.add_parser(
        "parse", help="Read and reconcile a PDF without contacting QuickBooks."
    )
    parse_command.add_argument("--pdf", required=True)

    split_command = subparsers.add_parser(
        "split", help="Find the matching QuickBooks deposit and prepare/apply its split."
    )
    split_command.add_argument("--pdf", required=True)
    split_command.add_argument("--deposit-id")
    split_command.add_argument(
        "--apply",
        action="store_true",
        help="Actually update QuickBooks. Without this flag the command is a dry run.",
    )
    split_command.add_argument(
        "--allow-resplit",
        action="store_true",
        help=(
            "Build a replacement plan even if the deposit is already split. "
            "Without --apply this is still only a dry run."
        ),
    )
    split_command.add_argument(
        "--approval-file",
        help=(
            "Approval JSON created from a reviewed screening dry run. Required "
            "before applying correction_preview results."
        ),
    )

    approve_command = subparsers.add_parser(
        "approve-screening",
        help=(
            "Create an approval JSON from a reviewed screening dry-run file. "
            "This does not contact or change QuickBooks."
        ),
    )
    approve_command.add_argument("--review-file", required=True)
    approve_command.add_argument("--approved-by", default="")
    approve_command.add_argument("--notes", default="")

    audit_split_command = subparsers.add_parser(
        "audit-split",
        help=(
            "Read a PDF and QuickBooks deposit, then compare amounts/accounts/"
            "customers without changing QuickBooks."
        ),
    )
    audit_split_command.add_argument("--pdf", required=True)
    audit_split_command.add_argument("--deposit-id")

    gmail_command = subparsers.add_parser(
        "gmail", help="Process matching PDF attachments from Gmail once."
    )
    gmail_command.add_argument("--max-results", type=int, default=25)
    gmail_command.add_argument(
        "--apply",
        action="store_true",
        help="Actually update QuickBooks. Without this flag the command is a dry run.",
    )

    history_command = subparsers.add_parser(
        "history",
        help="Audit several owner-statement PDFs together without contacting QuickBooks.",
    )
    history_command.add_argument(
        "--pdf",
        action="append",
        help="PDF to include in the history audit. Repeat this option for each PDF.",
    )
    history_command.add_argument(
        "--folder",
        help="Folder to scan recursively for owner-statement PDFs.",
    )
    history_command.add_argument("--start-year", type=int)
    history_command.add_argument("--end-year", type=int)

    qbo_deposit_command = subparsers.add_parser(
        "qbo-deposit",
        help="Read one QuickBooks deposit and its split lines without changing it.",
    )
    qbo_deposit_command.add_argument("--deposit-id", required=True)
    qbo_deposit_command.add_argument(
        "--raw",
        action="store_true",
        help="Include the full raw QuickBooks deposit JSON.",
    )

    qbo_deposits_command = subparsers.add_parser(
        "qbo-deposits",
        help="Read QuickBooks deposits in a date range without changing them.",
    )
    qbo_deposits_command.add_argument("--start", required=True, help="YYYY-MM-DD")
    qbo_deposits_command.add_argument("--end", required=True, help="YYYY-MM-DD")
    qbo_deposits_command.add_argument(
        "--amount",
        help="Optional exact deposit amount filter, for example 1012.50.",
    )
    qbo_deposits_command.add_argument(
        "--memo-keyword",
        help="Optional keyword filter, for example Keyrenter or SIGNONFILE.",
    )
    qbo_deposits_command.add_argument(
        "--raw",
        action="store_true",
        help="Include each full raw QuickBooks deposit JSON.",
    )

    qbo_accounts_command = subparsers.add_parser(
        "qbo-accounts",
        help="Read QuickBooks chart of accounts without changing it.",
    )
    qbo_accounts_command.add_argument(
        "--keyword",
        help="Optional keyword filter, for example Airbnb or Listing.",
    )
    qbo_accounts_command.add_argument(
        "--raw",
        action="store_true",
        help="Include each full raw QuickBooks account JSON.",
    )

    qbo_customers_command = subparsers.add_parser(
        "qbo-customers",
        help="Read QuickBooks customers without changing them.",
    )
    qbo_customers_command.add_argument(
        "--keyword",
        help="Optional keyword filter, for example Keyrenter or 742.",
    )
    qbo_customers_command.add_argument(
        "--raw",
        action="store_true",
        help="Include each full raw QuickBooks customer JSON.",
    )

    verify_history_command = subparsers.add_parser(
        "verify-history",
        help=(
            "Backtest: compare predicted splits against saved historical QuickBooks "
            "deposits. Read-only and offline (uses saved JSON, never contacts QBO)."
        ),
    )
    verify_history_command.add_argument(
        "--pairs-file",
        required=True,
        help="JSON file listing {pdf, deposit_file} pairs to compare.",
    )

    intake_command = subparsers.add_parser(
        "intake",
        help=(
            "Fetch new owner packets (Gmail + intake folder) and scan QuickBooks for "
            "unposted deposits. Dry run unless --apply; with --apply, only completely "
            "clean screenings auto-post — anything flagged queues for the review UI."
        ),
    )
    intake_command.add_argument(
        "--statements-folder",
        default=r"D:\Project Automation\Quickbooks Automation Testing\Owner Statements 2021-2026",
        help="Where standardized packets live (the review UI reads this too).",
    )
    intake_command.add_argument(
        "--intake-folder",
        help="Optional drop folder for manually downloaded packets (e.g. from AppFolio).",
    )
    intake_command.add_argument("--skip-gmail", action="store_true")
    intake_command.add_argument("--skip-scan", action="store_true")
    intake_command.add_argument("--days", type=int, default=120, help="How far back to scan deposits.")
    intake_command.add_argument("--max-results", type=int, default=25)
    intake_command.add_argument(
        "--apply",
        action="store_true",
        help="Actually stage files and auto-post clean screenings. Without this it only reports.",
    )

    appfolio_command = subparsers.add_parser(
        "fetch-keyrenter",
        help=(
            "Download owner statements from the Keyrenter AppFolio portal (browser "
            "automation) into an inbox folder and stage them like manual downloads. "
            "First run: --login (one-time manual sign-in, session saved). Staging is "
            "a dry run unless --apply."
        ),
    )
    appfolio_command.add_argument(
        "--login",
        action="store_true",
        help="Open a visible browser to log in once and save the portal session.",
    )
    appfolio_command.add_argument(
        "--dump",
        action="store_true",
        help="Save a screenshot + HTML of the statements page for diagnosis.",
    )
    appfolio_command.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window while fetching (useful for debugging).",
    )
    appfolio_command.add_argument(
        "--statements-folder",
        default=r"D:\Project Automation\Quickbooks Automation Testing\Owner Statements 2021-2026",
    )
    appfolio_command.add_argument("--max-downloads", type=int, default=12)
    appfolio_command.add_argument(
        "--apply",
        action="store_true",
        help="Actually stage downloaded statements into the statements folder.",
    )

    rename_command = subparsers.add_parser(
        "rename-packets",
        help=(
            "Rename owner-statement PDFs in a folder to "
            "'Owner Packet - <Provider> - <MM-YY> - <Amount>.pdf'. "
            "Dry run unless --apply; already-renamed files are skipped."
        ),
    )
    rename_command.add_argument("--folder", required=True)
    rename_command.add_argument(
        "--provider",
        choices=["Keyrenter", "Nashville"],
        help="Force the provider label (default: infer from folder/statement).",
    )
    rename_command.add_argument(
        "--apply",
        action="store_true",
        help="Actually rename the files. Without this flag it is a dry run.",
    )
    return parser


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()

    if args.command == "parse":
        result = parse_statement_pdf(args.pdf).to_dict()
    elif args.command == "history":
        if args.folder:
            result = scan_statement_folder(
                args.folder,
                start_year=args.start_year,
                end_year=args.end_year,
                extra_pdf_paths=args.pdf,
            )
        elif args.pdf:
            result = audit_statement_history(args.pdf)
        else:
            raise SystemExit("history needs either --folder or at least one --pdf.")
    elif args.command == "qbo-deposit":
        result = read_deposit(args.deposit_id, include_raw=args.raw)
    elif args.command == "qbo-deposits":
        result = read_deposits(
            args.start,
            args.end,
            amount=args.amount,
            memo_keyword=args.memo_keyword,
            include_raw=args.raw,
        )
    elif args.command == "qbo-accounts":
        result = read_accounts(keyword=args.keyword, include_raw=args.raw)
    elif args.command == "qbo-customers":
        result = read_customers(keyword=args.keyword, include_raw=args.raw)
    elif args.command == "approve-screening":
        result = create_screening_approval(
            args.review_file,
            approved_by=args.approved_by,
            notes=args.notes,
        )
    elif args.command == "verify-history":
        result = verify_history_from_file(args.pairs_file, load_config(args.config))
    elif args.command == "rename-packets":
        result = rename_packets(
            args.folder, provider=args.provider, apply=args.apply
        )
    elif args.command == "fetch-keyrenter":
        from app.appfolio import dump, fetch, login

        if args.login:
            result = login()
        elif args.dump:
            result = dump(headless=not args.headed)
        else:
            result = fetch(
                statements_folder=args.statements_folder,
                headless=not args.headed,
                max_downloads=args.max_downloads,
                apply=args.apply,
            )
    elif args.command == "intake":
        result = run_intake(
            load_config(args.config),
            statements_folder=args.statements_folder,
            intake_folder=args.intake_folder,
            skip_gmail=args.skip_gmail,
            skip_scan=args.skip_scan,
            days=args.days,
            max_results=args.max_results,
            apply=args.apply,
        )
    else:
        config = load_config(args.config)
        if args.command == "audit-split":
            result = audit_split(
                args.pdf,
                config,
                deposit_id=args.deposit_id,
            )
        elif args.command == "split":
            result = process_pdf(
                args.pdf,
                config,
                apply=args.apply,
                deposit_id=args.deposit_id,
                allow_resplit=args.allow_resplit,
                approval_file=args.approval_file,
            )
        else:
            result = process_gmail(
                config, apply=args.apply, max_results=args.max_results
            )

    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"\nSaved review file: {output_path.resolve()}")


if __name__ == "__main__":
    main()
