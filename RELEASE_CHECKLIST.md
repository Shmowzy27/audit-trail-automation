# Audit Trail — Version 1.0 Release Goals

A concrete checklist for reaching a production-validated **Version 1.0** of **Audit Trail**
(the QuickBooks owner-statement automation platform). It makes project completion
measurable: when every box is checked, the platform is 1.0-ready.

> Phases mirror [ROADMAP.md](ROADMAP.md). This file is the *measurable checklist*; the
> roadmap is the *narrative plan*. Items may be promoted here from
> [REVIEW_LATER.md](REVIEW_LATER.md) during planning.

Legend: `[ ]` not started · `[~]` in progress · `[x]` done

**Where we are (2026-07-03):** Phases 1–3 are **complete** — the parser/engine is stabilized
and the review web app is client-ready (audit history + in-app Sandbox/Production switch just
landed). Phase 4 (automation & intake) is **built and hardening**. The app is currently pointed
at **production** for the client demo. The remaining gap to 1.0 is operational, not features:
**one gated live apply + accountant sign-off**, plus packaging/docs (installer, CI, User Guide,
README/demo). See the per-phase boxes below.

---

## Phase 1 — Foundation  ✅ *(complete)*
- [x] Project architecture & modular Python codebase
- [x] PDF parser
- [x] QuickBooks Online integration
- [x] Historical reconciliation engine
- [x] Expert accounting rules
- [x] Screening (safety) engine
- [x] Approval workflow
- [x] Audit engine
- [x] Private GitHub repository
- [x] Engineering documentation

## Phase 2 — Stabilization  ✅ *(complete)*
- [x] Complete sandbox validation (deposits 148–151 split/applied/verified; 150 & 151 re-tested clean, 0 warnings)
- [x] Regression tests for validated splits (74 tests: parser, keyrenter, expert rules, screening, intake, appfolio, pdf-order, quickbooks, approvals, ui-server env-switch/audit guards)
- [x] Historical verification — cross-referenced vs the accountant's real 2021–2024 QBO splits (69% dollars-by-account; the parser fix raised it from 64.5%)
- [x] Confidence scoring for proposed categorizations (High/Medium/Low per line, surfaced in the UI)
- [x] Parser stabilization (74/74 parse + reconcile; single-source categorization; row-alignment fix BUG-019; multi-property regression fixtures)
- [~] Open accountant confirmations before full production trust: Admin Fee income-vs-expense, Property Cash Reserve account, security-deposit forfeiture, renewal-fee → Leasing Fee

## Phase 3 — Desktop / Web Application  ✅ *(complete)*
- [x] Approval Dashboard (local Flask web UI — pick statement, preview, gated apply)
- [x] Review UI (Review / edit / Approve / Reject) — editable Account/Customer (amounts locked), edit-to-unblock, re-check
- [x] Correction Preview view (proposed split beside the PDF, plus the current QuickBooks split)
- [x] Confidence indicators surfaced in the UI (per-line badges + aggregate)
- [x] Connectors panel — connect/disconnect Gmail, QuickBooks, Keyrenter from the UI (same-browser consent; wrong-environment company rejected)
- [x] PDF.js viewer with in-packet search (highlight + scroll to match); grouped toolbar, stepper, collapsible cards, split filter, toasts, printable apply confirmation, dark/light theme
- [x] Sandbox/Production clearly surfaced (pulsing PRODUCTION banner; environment guard on connect)
- [x] Audit Viewer — browse applied-split history in the UI (🧾 Audit history: every pre-apply backup, newest first; click a row for the exact lines; verification-mismatch flagged)
- [x] In-UI environment switch — click the env banner to switch Sandbox ⇄ Production (hot `.env` reload, no restart; entering Production requires a typed confirmation; refuses mid-connect; guards regression-tested)

## Automation & Intake  *(built; live-hardening)*
- [x] Gmail intake — new Nashville packets pulled into the statements folder (connected to the client's packets mailbox; self-healing dedupe)
- [x] Keyrenter AppFolio connector — headless portal login, ZIP unpacking, content-deduped staging (Playwright)
- [x] `intake` command — one pass: Gmail fetch + folder ingest + QBO unposted-deposit scan; clean-only auto-apply; dry-run by default
- [x] Deposit scan pairs unsplit QBO deposits to packets by amount + date window; ambiguous ties deferred to review
- [ ] Scheduled run (Windows Task Scheduler) once manual runs are boring
- [ ] Invoice attachments from AppFolio ZIPs onto QBO transactions (logged in REVIEW_LATER)

## Phase 4 — Production Readiness
- [ ] Installer / one-command startup
- [~] Logging (today: JSON run-outputs under `runtime/` + audit backups before every apply; no central log)
- [~] Diagnostics / error reporting (UI surfaces errors + connector status; no central log yet)
- [~] Production deployment process (go-live runbook written; environment guard enforces the right company; ngrok redirect for prod OAuth; one gated live apply still pending)
- [~] Backup strategy (pre-apply audit backups + off-repo conversation-transcript backups; formal data backup TBD)
- [ ] CI/CD running the test suite on every push

## Version 1.0 — Release readiness
- [~] Documentation complete and synchronized (CHANGELOG, BUG_LOG through BUG-021, this checklist current; ROADMAP/README pass pending)
- [~] Production validated against real statements (connected to live QBO read-only + previews; one gated live apply + accountant sign-off still pending)
- [x] Repository cleanup — no secrets tracked (`.env*`, `secrets/`, `config.json`, `runtime/` gitignored; conversation backups kept off-repo)
- [ ] User Guide written
- [ ] Demo video / recorded walkthrough
- [ ] Polished `README.md`
- [ ] Release readiness review

---

## How to use this checklist
- Update boxes as work completes; keep it honest (`[~]` for partial).
- When a box closes, add a corresponding entry to [CHANGELOG.md](CHANGELOG.md).
- Don't check **Version 1.0** items until the phase items above them are done — 1.0 means
  *production-validated and trustworthy*, not *feature-complete on paper*.

> Reminder from [ENGINEERING_PRINCIPLES.md](ENGINEERING_PRINCIPLES.md): correctness and
> safety gate the release. A polished UI on an unreliable engine is not 1.0.
