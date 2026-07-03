# Changelog

All notable changes to this project are recorded here. This is the project's official
release history going forward.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Note on early versions:** the project predates formal version tracking. Versions and
> dates for `0.1.0`–`0.7.0` are **reconstructed approximations** of milestones that already
> happened, provided so the history reads coherently. Dates are in 2026.

---

## [Unreleased]
Phase 2 — Stabilization (current focus). See [ROADMAP.md](ROADMAP.md) and
[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).

### Added
- **Review-UI product polish + connectors + in-app search.** The web UI got a brand header,
  grouped toolbar, welcome/empty state, progress stepper, collapsible cards, split-table
  filter, toast notifications, skeleton loading, and a **persistent dark/light theme** (all
  CSS-variable driven). A **🔌 Connectors panel** connects/disconnects Gmail, QuickBooks, and
  the Keyrenter portal from the UI (same-browser OAuth consent; QuickBooks rejects a
  wrong-environment company; disconnect clears the local sign-in for account switching). A
  **📥 Fetch new packets** button runs Gmail + portal intake in one click. The deposit lookup
  defaults to configured property-manager memos ("Keyrenter/Nashville only"). A **PDF.js
  viewer** (`ui/vendor/`) renders packets to high-DPI canvas and powers a **"search this
  packet"** box that highlights every match and scrolls to the current one (comma-insensitive;
  browser viewer kept as fallback).
- **🧾 Audit history viewer in the UI.** A new header button browses every applied split.
  Each apply writes a pre-write backup of the exact plan to `runtime/audit/`; the viewer
  lists them newest-first (deposit #, timestamp, total, line count) and expands any row into
  the full line detail (account, customer, amount) plus the original deposit. A post-write
  verification mismatch (`*-MISMATCH.json`) is flagged in red. Read-only; path-traversal
  refused and only its own backups are served (`/api/audits`, `/api/audit`).
- **🧭 In-UI environment switch (Sandbox ⇄ Production).** Click the environment banner to
  switch which QuickBooks company the app targets — it swaps the active `.env` for
  `.env.<target>` and hot-reloads it in-process, so **no restart** is needed. Entering
  Production requires typing `PRODUCTION` to confirm (upholds sandbox-before-production),
  refuses to switch while a connector sign-in is in flight, re-probes connector status
  against the new environment, and only ever reads the committed `.env.<target>` source
  files. The switch never touches the books; every apply still goes through screening,
  approval, and post-write verification (`/api/env/switch`). Safety guards regression-tested
  (`tests/test_ui_server.py`).
- **Browser end-to-end tests (Playwright).** A `pytest-playwright` smoke suite (`tests/e2e/`)
  launches the real Flask UI in a background thread — QuickBooks stubbed out (bad token path,
  so it never contacts live books), config from the tracked `config.example.json`, empty
  statement/audit dirs — and drives a headless Chromium browser to lock in the page shell and
  the recent UI features (brand title, environment banner + switch modal, connectors panel,
  audit-history viewer, dark/light theme). 7 tests; run with `pytest tests/e2e`. Test-only
  dependencies are captured in `requirements-dev.txt`.
- **`fetch-keyrenter` command — AppFolio owner-portal connector (staged upgrade).**
  AppFolio has no API, so this drives Chromium via Playwright: `--login` opens a visible
  browser for a one-time manual sign-in (email codes/2FA work naturally; session saved to
  `secrets/appfolio_state.json`), then plain runs reuse the session headlessly, download
  recent statement PDFs into `runtime/appfolio-inbox`, and stage them through the SAME
  content-deduped `ingest_folder` path as manual downloads (dry-run unless `--apply`).
  Expired sessions **self-heal**: the connector logs back in with `APPFOLIO_EMAIL` /
  `APPFOLIO_PASSWORD` from `.env` (selectors taken from the portal's real login form) and
  saves the refreshed session; device-verification walls are reported plainly. The portal
  delivers each period as a **ZIP containing the owner packet plus vendor invoices** —
  zips are unpacked with unique prefixes (identically named PDFs can't overwrite each
  other), packets stage, invoices are rejected as non-statements. Validated end-to-end
  against the live portal: 5 periods downloaded, 4 deduped, 1 new June 2026 packet staged.
  If no download links match after a page redesign, a screenshot + HTML dump is saved
  automatically for selector repair (`--dump` on demand). Playwright imported lazily.
- **`intake` command — the Phase 4 pipeline entry point.** One command runs three steps
  (each skippable, dry-run by default): fetch new owner-statement PDFs from **Gmail**
  straight into the statements folder under the standardized name (dedupes via
  `runtime/state.json`); **ingest** manually downloaded packets (e.g. from the Keyrenter
  AppFolio portal) from a drop folder; and **scan QuickBooks for unposted deposits** —
  recent deposits still on a single unsplit line are paired to their packet by amount +
  the standard date window and run through the normal screening pipeline. With `--apply`,
  only a **completely clean screening** (`ready_to_split`, zero warnings, no
  low-confidence lines) auto-posts; anything flagged is reported as queued for the review
  UI, and amount ties across overlapping months are reported as ambiguous instead of
  guessed (`app/intake.py`, 8 tests).
- **Edit-to-unblock in the review UI.** When a line has no account mapping (e.g. an
  unclassified `Other income` line), screening used to stop *before* building the split,
  leaving an empty table with nothing to fix. Now every line is shown as an editable row
  even while blocked — the unmapped one is flagged "needs account" — so the reviewer
  assigns an account and clicks **Re-check with my edits** to build the split and continue
  to review → approve → apply. The "unmapped" test is now per-line and override-aware in
  both `service.py` and `create_split_plan`, so an assigned account genuinely clears the
  block (`app/screening.py:build_unmapped_preview`, + tests). Amounts stay locked.
- **Renamed the primary UI action `Preview changes` → `Start automation`** and added a
  "Deposit found ✓ / none matched" indicator, so the flow reads: Start automation
  (auto-find deposit + build split) → review/edit → Apply (gated write).
- **`rename-packets` command** — standardizes statement PDF filenames to
  `Owner Packet - <Provider> - <MM-YY> - <Amount>.pdf`. Dry run by default, `--apply`
  to rename (writes a reversible old→new mapping under `runtime/`), and idempotent
  (already-renamed files are skipped) so it can be re-run as new packets arrive.
- **Local review-and-approve web UI (Phase 3 MVP)** — a thin Flask app (`ui/`) to pick a
  statement, see the **PDF beside the proposed split** (with per-line confidence badges and
  warnings that name the property/category), and **Apply** through the exact screening +
  approval + post-write verification path. Reviewers can **edit each line's Account/Customer**
  (chosen from the real QBO account/customer lists; amounts stay locked to protect
  reconciliation — invalid edits are blocked by screening), re-check, and see the **current
  QuickBooks split** (refreshes after apply). Shows the active environment; never bypasses a gate.
- **Confidence scoring** — every categorized line gets a High/Medium/Low level with an
  explicit driver (rule tier, review warnings, or lack of 2021-2024 accountant precedent),
  plus an aggregate summary in the expert-rule review. Ordinal and explainable by design
  (no made-up percentages); additive — does not change categorization or the apply path.
- **Historical verification (`verify-history`)** — offline, read-only backtest that
  compares our predicted split against saved historical QuickBooks deposit snapshots and
  reports per-category divergences (`app/history_verify.py`, + regression test). Initial
  run vs the 4 review deposits showed 89.6% line-level agreement.
- Deposit split fix + post-write verification (BUG-001); reuse existing line `Id` to
  replace rather than append.

### Fixed
- **Safety-device / filter maintenance lines were unclassified.** "Supply/Install
  smoke detectors, air filter pack" (May 2026, first occurrence) fell to `Other expense`.
  Now routes to **Repairs & Maintenance** per the accountant's directly analogous
  posting ("rekey locks, co detectors" → Repairs & maintenance, QBO deposit 402,
  Dec 2024). Placed after the cleaning rule so cleaning-context filter lines keep
  their 2023 Cleaning treatment. 2 regression tests.
- **Lease renewal fees were never classified.** The recurring $250 "Lease Renewal Fee"
  (6 occurrences, 2022–2026) had no expert rule and always fell to `Other expense` /
  review. Now routes to **Commissions & fees:Leasing Fee** — the accountant's majority
  and most recent practice (QBO deposits 226/2023, 398/2024; the single 2022 Admin Fee
  posting and the 2026 ad-hoc "Lease Renewal" account were considered and not adopted).
  2 regression tests; verified against all 6 historical occurrences.
- **Parser mislabeled rows from neighboring transactions' text (BUG-019).** The Keyrenter
  description was built from a 3-line window, so a transaction absorbed its neighbors' text
  — a rent receipt beside a "Transfer from …" row was read as an inter-property transfer
  (e.g. deposit 150's $498 rent and $168 management fee posted to the equity transfer
  account). The parser now describes each row from its **own** line, only folding in
  adjacent non-dated *wrap* lines for bare amount rows (a check/reference with no inline
  "Category - detail"), so genuinely-wrapped rows like security deposits still classify.
  Verified across all 74 statements: 35 lines corrected to their body-justified category,
  6 more now honestly flagged for review, **0 regressions**; regression test added.
- **Duplicate-amount warning over-flagged legitimate multi-property records (BUG-018).**
  The check grouped by amount alone, so identical rent on two units or one flat mowing fee
  across three properties fired a warning every time (deposit 151 showed 4 such false
  positives). It now groups by **(property, amount)** and only warns when the same amount
  repeats **within one property under different categories** — the sole case that may be a
  mislabel and that reconciliation can't catch. Deposit 151 now shows 0 warnings; 3 tests added.
- **Security-deposit / inter-property-transfer classification (BUG-017).** Tenant
  security-deposit bank transfers whose "Security Deposit Transfer" text was split by the
  PDF's date/reference/amount columns fell through to `Other income` and blocked the split
  (e.g. deposit 151's $1,850). The rule now matches the words individually and routes
  tenant deposits to `Security deposits` (a liability) while inter-property "transfer
  to/from another property" lines go to `Transfer funds` — matching the accountant's
  2021-2024 postings (QBO deposits 434 and 176). Verified across 143 historical
  transfer/deposit lines; 4 regression tests added.
- **Review UI listed statements month-first, not chronologically (BUG-016).** The picker
  sorted `MM-YY` filenames as raw text, grouping every April/May together. Added
  `app/pdf_order.py` (`pdf_sort_key`, parses real year/month) and sorted `/api/pdfs` by it,
  so the list reads `01-21 … 12-21, 01-22 …` per folder. 5 regression tests added.

### Changed
- **Parser stabilization — single source of truth for categorization.** Removed ~155
  lines of duplicated category logic from `parser.py`; the Keyrenter parser now emits raw
  kind-based categories and `expert_rules.py` is the sole categorizer. Eliminates the
  drift that let the parser and expert rules disagree. Verified: deposits 148/149 splits
  unchanged, 26 tests green.
- **Aligned category→account mappings and rules to the accountant's 2021–2024 Keyrenter
  practice** (the authoritative baseline): Mowing → `General business expenses:Landscaping`,
  Exterminator → `Repairs & maintenance:Exterminator`, Admin Fee → `…Property Management
  Fees:Admin Fee`, Property Reserve → `General business expenses:Property Cash Reserve`,
  added `Electricity` → `Utilities:Electricity`, Gas → `Utilities:Gas`. Expert rules now
  route plumbing/water work to **Plumbing** (and water-damage to Repairs & Maintenance)
  instead of a top-level Contract labor account the accountant never used. New expert-rule
  tests added.

### In progress
- End-to-end sandbox validation of deposits 148–151 (148 & 149 applied; 150/151 Friday demo).
- Confidence scoring (will calibrate off historical match rates).
- Line-level backtest vs the accountant's 2021–2024 deposits.

---

## [0.8.0] — 2026-06-29 — Documentation system
### Added
- `ENGINEERING_PRINCIPLES.md` — the project's engineering standard.
- `CHANGELOG.md` — this file.
- `RELEASE_CHECKLIST.md` — Version 1.0 release goals and completion checklist.
- `ROADMAP.md` — phased long-term plan (vision, single-client scope, phases 1–5).
- `COMMANDS.md` — operations runbook & command reference.
- `REVIEW_LATER.md` — deferred-observations backlog.
- Architecture diagrams (Mermaid) and a "Supported Providers" / provider-plugin
  section in `ARCHITECTURE.md`.
- Documentation map in `README.md`.

### Changed
- Git history cleaned of co-author trailers; solo-authorship convention adopted.

---

## [0.7.0] — 2026-06-28 — GitHub migration & repository consolidation
### Added
- Private GitHub repository; local `main` tracks `origin/main` over HTTPS.
- `.gitignore` protecting `.env*`, `secrets/`, `config.json`, `runtime/`, `.venv/`.

### Changed
- Canonical working copy consolidated to `D:\Project Automation\qbo-owner-statement-automation`.

### Removed
- Duplicate "Project Knowledge" folder.

### Security
- Verified no secrets or tokens are tracked; only `.example` templates are committed.

---

## [0.6.0] — 2026-06-27 — Approval workflow & audit trail
### Added
- `app/approvals.py` — fingerprinted approval files that let reviewed results pass the
  safety gate without weakening it.
- Audit backups written to `runtime/audit/` before any apply.
- `approve-screening` command.
- Tests for the approval workflow.

---

## [0.5.0] — 2026-06-27 — Screening safety gate
### Added
- `app/screening.py` — every `split` run produces a screening report before anything is
  posted; statuses: `ready_to_split`, `needs_review`, `already_split_matches`,
  `correction_preview`, `blocked`.
- `--apply` is blocked unless screening (or a matching approval) allows it.

---

## [0.4.0] — 2026-06-26 — Historical validation & expert accounting rules
### Added
- `app/expert_rules.py` — conservative rules learned from expert-posted 2021–2024
  QuickBooks history; normalizes ambiguous PDF labels to historical QuickBooks accounts.
- `app/history.py` and `app/split_audit.py` — multi-statement history audit and
  read-only comparison of expected vs. existing QuickBooks splits.
- Warning types: `unclassified_line`, `duplicate_or_similar_amounts`, `mixed_pdf_context`.

---

## [0.3.0] — 2026-06-26 — Keyrenter provider support
### Added
- Parser support for Keyrenter Springfield owner packets (in addition to Sample PM).
- Per-property-class → QuickBooks customer mapping in `config.json`.
- Keyrenter parser tests.

---

## [0.2.0] — 2026-06-25 — QuickBooks Online integration
### Added
- `app/quickbooks.py` — OAuth, deposit read/search, account/customer lookup, split-plan
  builder, and guarded apply.
- Read-only inspection commands: `qbo-deposit`, `qbo-deposits`, `qbo-accounts`,
  `qbo-customers`, `audit-split`.
- Dry-run-by-default `split`; `--apply` required to write.

---

## [0.1.0] — 2026-06-25 — Initial project & PDF parser
### Added
- Initial modular project structure and CLI (`main.py`).
- `app/parser.py` — extracts structured line items from Sample PM owner-statement
  PDFs and enforces PDF math reconciliation.
- Gmail attachment intake (`gmail` command) and first-time `setup.ps1`.
- Initial `README.md`, `ARCHITECTURE.md`, `PROJECT_CONTEXT.md`, `PROJECT_PROGRESS.md`.

---

[Unreleased]: https://github.com/Shmowzy27/Quickbooks-Owner-Statement-Automation/commits/main
