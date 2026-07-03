# Review Later — Observations & Backlog

**This file is intentionally not a to-do list.** Items are recorded here to avoid
interrupting the current task. During planning, items may be promoted to
[ROADMAP.md](ROADMAP.md), scheduled into a milestone (see
[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)), or closed if no longer relevant.

> How to use: when something minor comes up mid-task, add it here instead of fixing
> it immediately. Each item has **what**, **why it matters**, a **suggested action**,
> and a **priority**. Larger features live in [ROADMAP.md](ROADMAP.md); this file is for
> smaller fixes / tech-debt / things to verify.

Priority key: 🔴 correctness/safety · 🟡 quality/dev-experience · ⚪ cosmetic/optional

---

## Accounting correctness

- [ ] 🟡 **AppFolio zips include the month's vendor invoices — use them.** Each portal
  statement zip contains the owner packet **plus the underlying vendor invoice PDFs**
  (HVAC bills, work orders, utility invoices). Today `fetch-keyrenter` extracts them and
  the ingest correctly skips them as non-statements, but they sit unused in
  `runtime/appfolio-inbox`.
  *Why:* they're the source documents for the expense lines we post — attaching them to
  the matching QBO expense/deposit lines would make every split audit-proof.
  *Action:* after Phase 4 settles, design an invoice-attachment step (QBO Attachable API)
  keyed off the same statement month. *(Logged 2026-07-02.)*

- [~] 🔴→🟡 **"Property Reserve" entry — investigated, legitimate; now an accountant
  confirmation.** Traced through the parser (2026-07-03): the `Property Reserve` line is a
  **computed per-property cash-balance delta** (beginning − ending cash), not a bogus parser
  residual. It represents real retained cash and maps to a reserve account the accountant
  uses, and it reconciles. So it is **not a code bug**. What remains is a *treatment* question
  — which reserve account (`General business expenses:Property Cash Reserve` vs
  `Partner distributions`) — which now lives with the other open accountant confirmations in
  [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) Phase 2. *(Logged 2026-06-29; investigated & reclassified 2026-07-03.)*

- [x] 🟡 **Normalize the `Property management fees` config mapping.** ✅ Fixed 2026-07-03.
  `config.json` and `config.example.json` now map to the exact lowercase account
  `General business expenses:Property management fees` (and `…:Admin Fee`), so it no longer
  relies on case-insensitive matching. *(Logged 2026-06-29; done 2026-07-03.)*

## Testing

- [~] 🔴 **Regression tests for the verified splits.** *In progress.* Added
  `tests/test_keyrenter_split_regression.py` — an **anonymized** Keyrenter-profile split
  test (neutral property/customer names, real category→account mappings) locking in:
  category mappings, per-property customer routing, the Property Reserve blank-entity plug,
  reconciliation, and the BUG-001 `Id`-reuse fix. Verified end-to-end on sandbox 148/149.
  *Remaining:* extend to cover the **expert-rule corrections** (e.g. refrigerator→maintenance)
  and a parser-level fixture, so the whole pipeline (not just split planning) is regressed.
  *(Logged 2026-06-29; partially done same day.)*

- [ ] ⚪ **Pick one test runner.** Project uses `unittest` (VS Code task + docs), but a
  `.pytest_cache/` directory exists, implying pytest was also run.
  *Action:* standardize on `unittest`; remove/ignore the pytest cache, or formally adopt
  pytest. Don't leave both implied.
  *(Logged 2026-06-29.)*

## Documentation hygiene

- [ ] 🟡 **Reconcile stale references in `ARCHITECTURE.md`.** Its "Current sandbox issue"
  section and a few paths describe an earlier multi-folder workflow that no longer exists
  now that the project is a single git repo at `D:\Project Automation\...`.
  *Why:* misleading to a new contributor; contradicts the consolidated single-repo setup.
  *Action:* update those sections to the current single-repo paths/workflow.
  *(Logged 2026-06-29.)*

## Correctness / robustness (from BUG_LOG)

- [ ] 🟡 **Harden the re-split replace path.** The BUG-001 fix (reuse existing line `Id`s)
  fully covers splitting an *unsplit* single-line deposit (148–151). But re-splitting an
  *already-split* deposit into *fewer* lines could leave surplus original lines behind,
  since QBO deposit full-updates keep omitted lines. The post-write verification gate will
  catch the mismatch and refuse to report success, so it's safe — but the replace logic
  itself should be hardened for the many→fewer case. See [BUG_LOG.md](BUG_LOG.md) BUG-001.
  *(Logged 2026-06-29.)*

## Expert-rule tuning (from accountant alignment)

- [ ] 🟡 **Tune the Electricity rule keywords against real data.** The new rule matches
  `electric utility / electricity / electric - vacant / epb electric`, but it was written
  without seeing an actual electric line from these statements (the 2025 statements had gas,
  not electric). Verify against a real electric bill line and adjust keywords if needed.
  *(Logged 2026-06-29.)*
- [ ] ⚪ **Revisit water-damage vs plumbing split.** Water-damage/remediation now routes to
  generic `Repairs & Maintenance` and plumbing (heater/valve/pipe) to `Plumbing`. Confirm
  that split matches the accountant's intent once we run the 2021–2024 line backtest.
  *(Logged 2026-06-29.)*

## Parser / history diagnostics (from stabilization, 2026-06-30)

- [ ] ⚪ **Make the continuity report ignore property-lifecycle cases.** The `history`
  folder scan flagged 14 continuity "mismatches," but on review all were benign:
  properties **added** (Cedar St units first appear 2024-08), **renamed**
  (`742 Maple Ave` vs `…Unit A`), the **sold** 90 Birch Lane, or `None` (property
  absent in one month). Parsing itself is correct (62/62 parse + reconcile).
  *Action:* skip `None`/added/removed/renamed pairs so the report only surfaces genuine
  same-property balance discontinuities. Low priority (diagnostic only).
- [ ] ⚪ **Two overlapping date-range statements can collapse to one statement-month.**
  Month is derived from the range's start date → day 1, so e.g. two Sept-2021 periods both
  map to `2021-09`. Harmless for splitting (we match by deposit), but note it if statement
  months are ever used as unique keys.

## Developer experience / tooling

- [x] 🟡 **Screening should report ALL missing accounts at once.** ✅ Fixed 2026-07-03.
  `create_split_plan` (`app/quickbooks.py`) now resolves every configured account, collects
  **all** failures, and raises one `QuickBooksError` listing them together — so a fresh
  company's missing accounts surface in a single screening result instead of one-per-run.
  Covered by `tests/test_quickbooks.py::test_all_missing_accounts_reported_at_once`.
  *(Logged 2026-06-29; done 2026-07-03.)*

- [x] ⚪ **Add a `.gitattributes` for line endings.** ✅ Fixed 2026-07-03. Added
  `.gitattributes` normalizing text files to LF (with `*.ps1` CRLF, `ui/vendor/**` and
  binaries left untouched), silencing the `LF will be replaced by CRLF` warnings.
  *(Logged 2026-06-29; done 2026-07-03.)*

---

## Larger items tracked elsewhere (pointers)
- **Deposit 148 account-gap decision** (create all 8 missing accounts vs. remap 3:
  Gas Utility, Property Reserve, Accounting fees) — active work, see the memory note
  `current-work-status` and [ROADMAP.md](ROADMAP.md) Phase 2.
- **Statement intake** (Gmail + Keyrenter AppFolio owner portal) — post-Phase-2, see
  [ROADMAP.md](ROADMAP.md).
