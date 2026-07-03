# Audit Trail

> Turn property-management owner-statement PDFs into reviewed, reconciled
> QuickBooks Online deposit splits — safely, with a human approval gate.

**Audit Trail** turns a property-management owner-statement PDF into split lines on
an existing QuickBooks Online **Bank Deposit**.

> ℹ️ This public repository ships with **synthetic sample data** (fictional owners,
> properties, and tenants). It contains no real client or financial information.

For the supplied April 2026 statement, the parser produces:

- Five rental-income lines totaling `$3,343.20`
- Five negative expense lines totaling `-$1,450.87`
- A reconciled deposit total of `$1,892.33`

The program refuses to update QuickBooks unless the PDF totals reconcile and the
matching deposit total equals the statement's Net Income.

## Documentation

This repository is documented as a production-oriented accounting platform. Start here:

| Document | Purpose |
|---|---|
| [ENGINEERING_PRINCIPLES.md](ENGINEERING_PRINCIPLES.md) | The engineering standard — the "why behind how we build". |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, diagrams, layers, and provider-plugin architecture. |
| [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) | Product identity, vision, and engineering role/charter. |
| [ROADMAP.md](ROADMAP.md) | Phased long-term plan (vision, scope, phases 1–5). |
| [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) | Version 1.0 goals and measurable completion checklist. |
| [COMMANDS.md](COMMANDS.md) | Operations runbook & command reference (daily workflow, recovery). |
| [CHANGELOG.md](CHANGELOG.md) | Official release history. |
| [BUG_LOG.md](BUG_LOG.md) | Significant bugs: root cause, fix, thought process, prevention. |
| [CLIENT_OVERVIEW.md](CLIENT_OVERVIEW.md) | Plain-English, non-technical overview (for clients). |
| [DEMO.md](DEMO.md) | Step-by-step runbook for the live sandbox demo. |
| [REVIEW_LATER.md](REVIEW_LATER.md) | Deferred-observations backlog (not a to-do list). |

## Open and run it in VS Code

1. Clone the repository:

   ```bash
   git clone https://github.com/Shmowzy27/audit-trail-automation.git
   cd audit-trail-automation
   ```
2. In VS Code, choose **File > Open Folder** and open the `audit-trail` folder.
3. Install the recommended Microsoft Python extension when VS Code prompts you.
4. Open **Terminal > Run Task**, then select **First-time setup**.
5. Edit `.env` and `config.json`.
6. Open the **Run and Debug** panel on the left.

The included VS Code run options are:

- **1. Parse supplied PDF (safe)** - reads only the PDF.
- **2. Preview QuickBooks split (safe)** - reads QuickBooks and shows the
  existing deposit beside the proposed update.
- **3. Preview Gmail attachments (safe)** - reads matching Gmail PDFs.
- **4. Authorize QuickBooks** - opens the Intuit sign-in page.
- **5. APPLY split to QuickBooks (writes data)** - the only supplied option that
  changes the deposit.

The safe preview creates `runtime/last-qbo-preview.json`. Inspect its
`original_deposit` and `update_payload` sections before using the APPLY option.
You can place breakpoints in:

- `app/parser.py` to inspect PDF extraction.
- `app/quickbooks.py` in `create_split_plan()` to inspect mappings and lines.
- `app/quickbooks.py` in `apply_split_plan()` immediately before the API update.

## Important safety behavior

- QuickBooks changes are disabled unless you add `--apply`.
- It expects exactly one unsplit `DepositLineDetail`; already-split deposits are
  skipped.
- If more than one deposit has the same amount, it stops instead of guessing.
- The original QuickBooks response and proposed update are saved under
  `runtime/audit/` before a change.
- Gmail message IDs are stored under `runtime/state.json` after a successful
  update, preventing duplicate processing.

Test this with a QuickBooks **sandbox company first**. Account and customer names
must exactly match your QuickBooks chart of accounts.

## 1. Install

Python 3.11 or newer is recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
Copy-Item config.example.json config.json
```

## 2. Configure QuickBooks Online

1. Create an app in the
   [Intuit Developer Portal](https://developer.intuit.com/app/developer/homepage).
2. Enable the QuickBooks Online Accounting scope.
3. Add `http://localhost:8000/callback` as a redirect URI.
4. Put the app's client ID and secret in `.env`.
5. Keep `QBO_ENVIRONMENT=sandbox` during testing.
6. Authorize the company:

```powershell
python authorize_quickbooks.py
```

The token is saved in `secrets/qbo_token.json` and refreshed automatically.

## 3. Configure Gmail

1. Create a Google Cloud project and enable the Gmail API.
2. Configure the OAuth consent screen.
3. Create a **Desktop app** OAuth client.
4. Download its JSON file to `secrets/google_credentials.json`.

The first `gmail` command opens a browser so you can approve read-only Gmail
access. The resulting token is saved in `secrets/google_token.json`.

## 4. Confirm account mappings

Edit `config.json`. In particular, confirm that these names exactly match the
customer and accounts in your QuickBooks company:

```json
{
  "quickbooks_customer": "500 Oak Street",
  "category_accounts": {
    "Rental Income, Airbnb": "Sales:Rental Income",
    "Job Supplies expense": "Supplies",
    "Listing Site Host Fees": "Advertising & marketing:Listing fees",
    "Office Supplies & Software": "Utilities:Internet & TV services",
    "PM Fees": "General business expenses:Property management fees",
    "Repairs & Maintenance": "Repairs & maintenance"
  }
}
```

You can add additional properties and statement-category mappings to the same
file.

## 5. Parse the PDF locally

This does not contact Gmail or QuickBooks:

```powershell
python main.py parse --pdf "C:\path\to\Owner Statement.pdf"
```

Add `--output runtime\my-review.json` before `parse` or `split` to save everything
shown in the terminal to a reviewable JSON file.

## 6. Preview the QuickBooks split

This reads QuickBooks but does not modify it:

```powershell
python main.py --config config.json split `
  --pdf "C:\path\to\Owner Statement.pdf"
```

If multiple deposits have the same Net Income amount, copy the correct
QuickBooks deposit ID from the error and rerun with `--deposit-id ID`.

## 7. Apply the split

Only after reviewing the dry-run output:

```powershell
python main.py --config config.json split `
  --pdf "C:\path\to\Owner Statement.pdf" `
  --apply
```

## 8. Process Gmail

Dry run:

```powershell
python main.py --config config.json gmail
```

Apply:

```powershell
python main.py --config config.json gmail --apply
```

The Gmail command is intentionally a one-shot task. After testing, schedule it
with Windows Task Scheduler every 10-15 minutes. This is simpler and more
reliable for a single mailbox than running a permanent server.

## What may need adjustment

QuickBooks account names vary by company. The example mappings are illustrative,
so the final names must be checked in your chart of accounts. Also, this parser
is tailored to the structured Keyrenter/Nashville owner-statement layout. If
future PDFs are scanned images or use a different layout, add OCR or a second
parser before allowing automatic posting.

## License

Released under the [MIT License](LICENSE).
