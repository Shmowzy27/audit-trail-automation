# Bug Log

A record of significant bugs found in this project: the symptom, how we found it,
the **thought process** while diagnosing, the root cause, the fix, and how we prevent
it from recurring. This is institutional memory — future contributors (and future us)
should be able to learn from each entry without having lived through it.

This complements the other docs:
- [REVIEW_LATER.md](REVIEW_LATER.md) — deferred observations (not yet bugs).
- [CHANGELOG.md](CHANGELOG.md) — what shipped and when.
- [ENGINEERING_PRINCIPLES.md](ENGINEERING_PRINCIPLES.md) — the rules that prevent whole classes of bugs.

**Entry template**
```
## BUG-NNN — <short title>
- Date / Severity / Status
- Symptom: what was observed
- How we found it: the trigger
- Investigation & thought process: what we suspected and why, what we ruled out
- Root cause: the actual underlying defect(s)
- Fix: what we changed
- Prevention: tests/guards/process so it can't silently return
- Lessons: the takeaway
```

---

## BUG-001 — Sandbox deposit split doubled the deposit (append instead of replace), and the apply falsely reported success
- **Date:** 2026-06-29
- **Severity:** High (accounting correctness — wrong dollar total posted)
- **Status:** ✅ **Resolved 2026-06-29** — verified end-to-end on sandbox deposit 148: applied to exactly **37 lines / $4,496.33**, original line replaced in place (not appended), verification gate passed. 9/9 unit tests green.
- **Scope:** this entry covers **both** linked defects in the apply path — (A) lines appended instead of replaced, and (B) the missing post-write verification. **It is the single canonical record of the post-write verification gate; do not open a separate bug for "apply should verify its result."**

### Symptom
The first real `split --apply` on sandbox deposit 148 produced a deposit with **38 lines totaling $8,992.66** — exactly **double** the correct net of **$4,496.33**. The CLI reported `"status": "updated"` as if it had succeeded.

### How we found it
Manual review of the deposit in QuickBooks right after applying. The "Other funds total" read $8,992.66, and the original single line (`Sales:Rental Income … "SIGONFILE" … $4,496.33`) was still present *alongside* the 37 new split lines.

### Investigation & thought process
1. **Compared plan vs. result.** The `plan.update_payload` contained exactly 37 lines summing to $4,496.33 with `replacing_existing_line_count: 1` — i.e., the planner built a correct *replacement*. But `result` showed 38 lines / $8,992.66. So the planner was right; the **write** was wrong.
2. **38 = 1 + 37.** The math made the failure mode obvious: the original line was *kept* and the 37 were *appended*, not substituted.
3. **Read the apply code.** [`apply_split_plan`](app/quickbooks.py) POSTs a full update (`sparse: false`, fresh `Id`/`SyncToken`, original line omitted). Per Intuit's general docs, a full update *clears* omitted fields/lines — so by the book this should have replaced. It didn't.
4. **Ruled out stale SyncToken.** The apply reads the deposit fresh and builds the payload in the same call (verified in [`service.py`](app/service.py)); no 400 error occurred, so the token wasn't stale.
5. **Confirmed via the official reference** that full-update *should* replace — but the Deposit page was truncated and community guidance noted deposits can *append* line items in practice. Empirically, our by-the-book full update appended, so we stopped trusting "omit-to-delete" for deposits.
6. **Spotted the deeper, scarier defect:** the apply never checked its own result. It reported `"updated"` at $8,992.66 without comparing to the plan. The screening gate guards the *way in*; there was **no gate on the way out**.

### Root cause
Two linked defects:
- **A — append, not replace.** QuickBooks deposit full-updates keep existing lines that are simply omitted from the payload, so relying on omission to delete the original line caused the 37 new lines to be added alongside it.
- **B — no post-write verification.** `apply_split_plan` assumed the API call did what was intended and reported success without validating the resulting total/line count.

### Fix
- **A:** In `create_split_plan`, reuse each existing line's `Id` on the new lines (`new_line["Id"] = existing["Id"]`) so QuickBooks updates the original line *in place* and adds the rest — leaving nothing to append. (Defensive: only when the existing line actually has an `Id`.)
- **B:** In `apply_split_plan`, after the write, re-read the returned deposit and compare its line count and total to the plan. On mismatch, write a `…-MISMATCH.json` and **raise** instead of reporting success.

### Prevention
- **Regression test** `test_split_reuses_existing_line_id_to_replace_not_append` asserts the first new line carries the existing `Id`.
- The **post-write verification gate** means any future replace/append regression fails loudly instead of silently corrupting the books.
- Reinforces [ENGINEERING_PRINCIPLES.md](ENGINEERING_PRINCIPLES.md) #7 (never silently modify) and #9 (explicit validation over assumptions).

### Lessons
- **Verify the way out, not just the way in.** A write to an external system must be confirmed, not assumed.
- **Vendor docs describe the happy path; trust the observed behavior.** "Should replace" ≠ "did replace."
- **Sandbox-first earned its keep** — this was caught before a single cent touched real books.

### Known follow-up (logged in [REVIEW_LATER.md](REVIEW_LATER.md))
The `Id`-reuse fix fully covers the unsplit case (deposits 148–151). Re-splitting an *already-split* deposit into *fewer* lines could still leave surplus original lines; the verification gate will catch it, but the replace path should be hardened for that case later.

---

## BUG-002 - Missing QuickBooks environment credentials blocked QBO commands
- Date: 2026-06-29
- Severity: Setup / High
- Status: Resolved
- Symptom: Commands failed with `Missing required environment variable: QBO_CLIENT_ID`.
- Root cause: `.env` did not yet contain QuickBooks app credentials.
- Fix: Added local `.env` values and kept `.env` ignored by Git.
- Prevention: Keep `.env.example` as the safe template; never commit real credentials.

## BUG-003 - QuickBooks redirect URI mismatch during authorization
- Date: 2026-06-29
- Severity: Setup / High
- Status: Resolved
- Symptom: Intuit showed `redirect_uri query parameter value is invalid`.
- Root cause: The redirect URI in `.env` did not exactly match the URI registered in Intuit Developer.
- Fix: Matched the registered redirect URI exactly; production required an HTTPS/ngrok URL.
- Prevention: Keep sandbox and production redirect URIs clearly separated.

## BUG-004 - Sandbox vs production token/environment confusion
- Date: 2026-06-29
- Severity: High
- Status: Resolved / Guarded
- Symptom: QBO returned object not found, no sandbox companies, or authorization errors.
- Root cause: Sandbox and production use different companies, realms, tokens, and sometimes credentials.
- Fix: Used separate token files and environment settings.
- Prevention: Always confirm `QBO_ENVIRONMENT`, token file, and company before running.

## BUG-005 - Deposit search could not find matching deposits
- Date: 2026-06-29
- Severity: Medium
- Status: Guarded
- Symptom: `No deposit for <amount> was found between <date range>`.
- Root cause: The matching deposit did not exist, was in another company/environment, had a different amount, or needed a direct deposit ID.
- Fix: Added direct `--deposit-id` workflow and screening total checks.
- Prevention: Screening blocks when the QBO deposit total does not match the PDF total.

## BUG-006 - Missing QuickBooks accounts/customers caused split planning failures
- Date: 2026-06-29
- Severity: High
- Status: Resolved / Guarded
- Symptom: Errors like `Expected one QuickBooks account/customer named ... but found 0`.
- Root cause: Config names must match QuickBooks fully qualified names exactly, and sandbox lacked some accounts/customers.
- Fix: Added account/customer inspection helpers and improved config mappings.
- Prevention: Use `qbo-accounts` and `qbo-customers` before applying new mappings.

## BUG-007 - Keyrenter statement month parsing failed or picked the wrong month
- Date: 2026-06-29
- Severity: High
- Status: Resolved
- Symptom: `Could not find the statement month`, or March 2026 was interpreted incorrectly.
- Root cause: Keyrenter PDFs use date-range formats different from Nashville statements.
- Fix: Parser was updated to recognize Keyrenter owner statement ranges.
- Prevention: Keep Keyrenter parser regression tests.

## BUG-008 - Nashville May 2025 did not reconcile
- Date: 2026-06-29
- Severity: High
- Status: Resolved
- Symptom: Parser said income/net income did not reconcile.
- Root cause: The parser missed a Nashville income line and categorized Airbnb host fees inconsistently.
- Fix: Parser was updated so Airbnb host fees map to `Listing Site Host Fees`.
- Prevention: Historical Nashville folder scan must stay at 0 failed files.

## BUG-009 - Ambiguous Keyrenter categories became `Other expense` / `Other income`
- Date: 2026-06-29
- Severity: High
- Status: Guarded
- Symptom: Split planning stopped with missing mappings for `Other expense` or `Other income`.
- Root cause: Some PDF descriptions were too vague for simple category matching.
- Fix: Added expert rules and safety blocking when confidence is low.
- Prevention: Unknown categories must stop for review instead of guessing.

## BUG-010 - Broad keyword rules were too aggressive
- Date: 2026-06-29
- Severity: High
- Status: Guarded
- Symptom: Similar words like transfer, maintenance, cash, reserve, or insurance could point to the wrong account.
- Root cause: Single-keyword matching was not reliable enough for accounting decisions.
- Fix: Rules now require stronger context from amount, property, description, historical pattern, and source category.
- Prevention: Prefer blocking over guessing; warnings must be reviewed.

## BUG-011 - QBO split labels differed even when totals matched
- Date: 2026-06-29
- Severity: High
- Status: Guarded
- Symptom: Audit showed totals and line amounts matched, but account/customer labels differed.
- Root cause: Existing QBO splits sometimes used different labels than expert-rule expectations.
- Fix: Added split audit and correction preview.
- Prevention: Label differences require review/approval before applying.

## BUG-012 - Screening approval refused blocked sandbox previews
- Date: 2026-06-29
- Severity: Medium
- Status: Resolved (working-as-designed + edit-to-unblock, 2026-07-03)
- Symptom: Approval returned `not_approved` even when totals matched.
- Root cause: Screening status was still `blocked`; approval only accepts approved-safe screening states. This is the safety gate behaving correctly — a `blocked` screening (e.g. an unmapped line with no account) must not be applied.
- Fix: Confirmed the refusal is correct behavior (do NOT weaken the gate). The real gap was that there was no way to *clear* the block without editing config and re-running. That is now handled by **edit-to-unblock** in the review UI: assign an account per line (overrides passed to `create_split_plan`), which turns the screening `ready_to_split` and lets approval proceed. Related: `create_split_plan` now reports **all** missing accounts at once (see REVIEW_LATER "report ALL missing accounts") so the block lists every gap in one pass.
- Prevention: Do not bypass approval. Rerun screening and inspect reasons; use edit-to-unblock in the UI to resolve unmapped lines before apply.

## BUG-013 - Git risk: duplicate Project Knowledge folder was uploaded
- Date: 2026-06-29
- Severity: Medium
- Status: Resolved
- Symptom: Duplicate `Project Knowledge` folder appeared in GitHub.
- Root cause: Documentation copy was inside the repo and got committed.
- Fix: Removed duplicate folder from GitHub and local project.
- Prevention: Use `git status --short` and `git diff --cached --name-status` before every push.

## BUG-014 - Git risk: local/private assistant folder appeared as untracked
- Date: 2026-06-29
- Severity: Low
- Status: Guarded
- Symptom: `.claude/` appeared as untracked.
- Root cause: Local assistant memory/config was inside the project folder.
- Fix: Add `.claude/` to `.gitignore`.
- Prevention: Keep local tool memory out of Git unless intentionally documented.

## BUG-015 - ngrok setup blocked production OAuth testing
- Date: 2026-06-29
- Severity: Setup / Medium
- Status: Resolved
- Symptom: `ngrok` was missing, unauthenticated, or too old.
- Root cause: ngrok was not installed/configured for the account.
- Fix: Installed/updated ngrok and added auth token.
- Prevention: Keep OAuth callback setup documented before production authorization.

---

## BUG-016 — Review UI listed statements month-first instead of chronologically
- **Date:** 2026-07-01
- **Severity:** Low (UX / reviewer ergonomics — no accounting or data impact)
- **Status:** ✅ **Resolved 2026-07-01** — picker now reads `01-21, 02-21, … 12-21, 01-22, …` per folder; 5 regression tests green (37 total).

### Symptom
After standardizing filenames to `Owner Packet - <Provider> - <MM-YY> - <Amount>.pdf`,
the statement picker in the review UI listed packets grouped by **month** — `04-21,
04-22, 04-23, 04-24, 05-21, 05-22, …` — instead of in date order. A reviewer scanning
for "the next month" saw four different years' Aprils in a row.

### How we found it
Spotted by the user in the UI dropdown right after the rename landed (screenshot of the
Keyrenter list showing `04-23, 04-24, 04-25, 04-26, 05-21, …`).

### Investigation & thought process
1. **Where does the order come from?** The frontend renders `/api/pdfs` in the exact
   order received (`for (const p of data.pdfs)` in [index.html](ui/index.html)); it does
   no sorting of its own. So the order is the server's.
2. **Read `/api/pdfs`.** It used `sorted(STATEMENT_FOLDER.rglob("*.pdf"))` — i.e. sorted
   the `Path` objects, which compares **filename text**.
3. **The trap:** `MM-YY` puts the month *before* the year, so a lexicographic (text) sort
   orders by month first (`04-*` before `05-*`), then by year within each month. The
   filenames are correct; the **sort key** was wrong.
4. **Ruled out the rename tool** — the on-disk names are exactly what we asked for. This
   is purely a *display ordering* defect, introduced when the new `MM-YY` names made the
   pre-existing text sort visibly wrong (older arbitrary names had masked it).

### Root cause
Sorting a **human date format** (`MM-YY`) as raw text. Lexicographic order only equals
chronological order when the most-significant field (year) comes first; with month first,
text sort ≠ time order.

### Fix
- Added `app/pdf_order.py` with `pdf_sort_key`, which parses `(year, month)` (and amount
  as a tiebreak) from the standardized name and sorts **folder → year → month → amount →
  name**; non-standard names sort last so a not-yet-renamed file can't jump into the middle.
- `/api/pdfs` now sorts with `key=pdf_sort_key` ([ui/server.py](ui/server.py)).
- Anchored the amount pattern to `\.pdf$` so the tiebreak doesn't capture the trailing dot.

### Prevention
- **Regression tests** `tests/test_pdf_order.py` (5): month-first ordering, year rollover,
  same-month amount tiebreak, unstandardized-name-last, folder grouping.
- Logic lives in a **pure, importable module** (not buried in the Flask app) so it stays
  unit-testable without spinning up a server.

### Lessons
- **Separate the display format from the sort key.** `MM-YY` is fine to *show*; never sort
  by it. Any time a date is embedded in a sortable string, sort by a parsed date, not text.
- A format chosen for **readability** (`MM-YY`) and one chosen for **sortability**
  (`YYYY-MM`) are different jobs; when you pick readability, own the sort in code.

---

## BUG-017 — Security-deposit lines blocked (and inter-property transfers mislabeled) due to phrase-only matching
- **Date:** 2026-07-01
- **Severity:** Medium (accounting correctness — a liability was landing in income, or blocking)
- **Status:** ✅ **Resolved 2026-07-01** — deposit 151's $1,850 now auto-classifies to `Security deposits`; the split unblocks with no manual mapping. Verified across 143 historical transfer/deposit lines (0 left unclassified); 45 tests green.

### Symptom
Deposit 151 (Sep 2025) was **blocked** with "Missing QuickBooks account mapping for: Other
income." The offending line was a **$1,850 tenant security deposit** at 88 Birch Lane
that the parser left as `Other income`.

### How we found it
The review UI blocked deposit 151; investigating the unmapped line (during the edit-to-unblock
work) revealed the packet text: `…Security 09/25/2025 #12492832 1,850.00 2,150.00 Transfer
Deposit Transfer`.

### Investigation & thought process
1. **There was already a rule** for `security deposit transfer` → `Security deposits`
   (`expert_rules.py`). Why didn't it fire?
2. **The PDF's columns interleave the phrase.** `expert_rules` matched the *contiguous*
   string "security deposit", but pdfplumber emits "Security <date> <ref> <amount> <balance>
   Transfer Deposit Transfer" — the words are split by the running-balance columns, so no
   contiguous "security deposit" exists. The 2024 `$2,520` line *did* keep the words adjacent,
   which is why that one classified and this one didn't.
3. **Checked the history for precedent** (`runtime/qbo-keyrenter-deposits-2021-2026.json`).
   The accountant booked a *tenant* deposit (dep 434, 2024, $2,520) to **Security deposits**,
   but a *"Security Deposit Transfer between properties"* (dep 176, 2023, $490.08) to
   **Partner investments:Transfer funds to other property account**.
4. **Spotted a second, latent defect:** the old rule matched "security deposit" *before* the
   transfer rule, so the 2023 $490.08 inter-property leg (which also says "Security Deposit
   Transfer") was mislabeled `Security deposits` instead of `Transfer funds`.

### Root cause
Two linked defects in `recommended_category_for_entry`:
- **A — phrase-only match.** Matching the contiguous string "security deposit" missed lines
  where PDF extraction interleaves the words with date/reference/amount tokens.
- **B — wrong precedence.** The security-deposit rule ran before the transfer rule, so an
  inter-property "Security Deposit **Transfer**" was booked as a tenant deposit.

### Fix
- `is_tenant_security_deposit(text)` matches "security" and "deposit" **individually** (order-
  and gap-independent), so interleaved rows classify.
- `is_interproperty_transfer(text)` ("transfer to"/"transfer from") **excludes** internal
  transfers from the security-deposit rule, so they route to `Transfer funds`. Both accounts
  were already mapped in `config.json`.

### Prevention
- **4 regression tests** (`tests/test_expert_rules.py::SecurityDepositAndTransferTests`):
  interleaved deposit, contiguous deposit, inter-property "transfer from", and "transfer to".
- Validated end-to-end over all 143 historical transfer/deposit lines — none left unclassified.

### Lessons
- **Don't phrase-match text a PDF laid out in columns.** Extraction interleaves column values;
  match the salient words, not a fixed string.
- **Two similar-looking events can have different correct accounts.** A tenant deposit
  (liability) and an inter-property transfer (equity) both say "Security Deposit Transfer";
  precedence and an explicit discriminator matter.
- **A rule sets the right *default*, not the last word.** The accountant once kept a forfeited
  $2,400 deposit as an Admin Fee ("water left running after move out"); such reclassifications
  are per-case judgments the reviewer makes in the UI — the rule shouldn't try to guess them.

---

## BUG-018 — Duplicate-amount warning over-flagged legitimate multi-property records
- **Date:** 2026-07-01
- **Severity:** Low (review noise — no data impact, but desensitizes the reviewer)
- **Status:** ✅ **Resolved 2026-07-01** — deposit 151 dropped from 4 spurious warnings to 0; 3 regression tests added (48 total).
- **Symptom:** On deposit 151 the review UI showed 4 `duplicate_or_similar_amounts` warnings for entirely normal records: identical $540/$560 rent on two units, one $55 mowing fee on three properties (each mowed 3× on distinct checks 2405/2431/2447), and a coincidental $200 (Door Installation vs Dishwasher Installation on different properties).
- **How we found it:** User asked why known, legitimate records were being flagged.
- **Root cause:** `build_duplicate_and_context_warnings` grouped by **amount alone**, then warned on any repeat with a differing category *or* property. On a consolidated multi-property statement the same amount recurs constantly across units, so the check fired on coincidences — noise, not errors. Reconciliation already guarantees the line total, so a true double-count can't slip through here.
- **Fix:** Group by **(property, amount)** and warn only when the same amount repeats **within one property under different categories** — the one scenario that may be a genuine mislabel. Cross-property/cross-unit repeats and same-category repeats (e.g. 3 real mowings) no longer warn.
- **Prevention:** `tests/test_expert_rules.py::DuplicateAmountWarningTests` — cross-property not flagged, same-category-on-one-property not flagged, same-property-different-category flagged.
- **Lessons:** A warning that fires on normal data trains reviewers to ignore it. Tune duplicate detection to the shape of the data (multi-property statements repeat amounts by design), and lean on reconciliation for the totals.

---

## BUG-019 — Parser mislabeled transactions with their neighbors' text (3-line description window)
- **Date:** 2026-07-01
- **Severity:** Medium (accounting correctness — income posted to the wrong account, but reconciled totals hid it)
- **Status:** ✅ **Resolved 2026-07-01** — verified across all 74 statements (35 lines corrected, 6 more now flagged for review, 0 regressions); regression test added (49 total).

### Symptom
On deposit 150 (Aug 2025) a `$498` **rent** receipt and a `$168` **management fee** were
classified as `Transfer funds` (the equity inter-property transfer account). The totals
still reconciled, so it wasn't caught by the math — only by the `mixed_pdf_context` warning.

### How we found it
Verifying the demo deposits after BUG-017/018: 150 still showed 6 `mixed_pdf_context`
warnings. Inspecting them, two lines were genuinely misclassified — the warning was a true
positive, not noise.

### Investigation & thought process
1. **Compared parser category vs the raw PDF.** The $498 row's own text says "Receipt Rent
   Income"; the $168 row says "Management fees" — neither says "transfer". So the transfer
   label came from *somewhere else*.
2. **Read `parse_keyrenter_statement_lines`.** The description was built from a **3-line
   window** (`lines[index-1 : index+2]` — previous, current, next). The $498 rent row sits
   directly above a "Transfer from 88 Birch Lane" row, so "transfer" bled into its
   description and the expert rule read it as an inter-property transfer.
3. **Why the window existed:** some rows are genuinely multi-line — a security deposit's
   amount sits on a bare `[dated]` line (just a reference like `#12492832`) while its
   "Security Deposit Transfer" description wraps onto the non-dated lines above and below.
   The window captured those, but also swept in neighboring *transactions*.
4. **Found the discriminator.** A row whose own body already contains a "Category - detail"
   phrase (e.g. "Rent Income - March", "Management fees - …") is self-describing; only a
   *bare* amount row (a check/reference, no " - ") needs the wrapped lines.

### Root cause
The description context pulled in adjacent lines indiscriminately. Neighboring **dated**
rows are separate transactions, and non-dated wrap lines can belong to the *following*
transaction — either way their category keywords leaked into the wrong row.

### Fix
`parse_keyrenter_statement_lines` now builds each row's description from its own line, and
folds in adjacent non-dated wrap lines **only when the row's body is bare** (no inline
" - " description). Dated neighbors are never included. Self-describing rows (rent, fees,
utilities) are read in isolation; bare rows (security deposits, move-out refunds) still
capture their wrapped description.

### Prevention
- **Regression test** `tests/test_keyrenter_parser.py::KeyrenterDescriptionAlignmentTests`:
  a rent row above a transfer row stays rent, a management fee beside a deposit's wrap line
  stays a fee, and a bare `#…` amount row still classifies as a security deposit.
- **Portfolio backtest** (before/after over all 74 statements): every one of the 35+ changed
  lines moved to its own body-justified category or to a flagged `Other income/expense`.

### Lessons
- **A reconciled total hides category errors.** The amounts summed correctly while income
  sat in an equity account; only a content warning surfaced it. Keep the `mixed_pdf_context`
  check — here it was a true positive.
- **Give each record only its own evidence.** Pulling in "context" from neighbors is
  convenient but lets one transaction relabel another; scope description text to the row,
  and treat multi-line layouts explicitly (bare-row → wrap) rather than with a blind window.

---

## BUG-020 — Light mode: key values were invisible (hardcoded white text)
- **Date:** 2026-07-02
- **Severity:** Low (readability — no data impact)
- **Status:** ✅ **Resolved 2026-07-02** — value text now theme-aware (`--text-strong`); light mode legible.
- **Symptom:** After adding the light/dark toggle, the review-UI summary bar showed the
  **property / month / net / deposit** values as near-blank — the whole screen read "washed out."
- **How we found it:** User screenshot of light mode; the bold values were unreadable.
- **Root cause:** `.sb-item b` and `.kv b` set `color: #fff` (hardcoded white). Fine on the dark
  panel, invisible on the light one. The CSS-variable theming didn't cover these two hardcoded spots.
- **Fix:** Introduced `--text-strong` (white in dark, near-black `#0d1826` in light) and used it for
  those bold values. Also bumped light-mode surface contrast (greyer content backdrop, stronger
  borders) so cards separate from the background.
- **Prevention:** Never hardcode `#fff`/`#000` for text once themes exist — always route through a
  variable. The static UI checker greps the `<style>` block; consider flagging raw `color:#fff` on text.
- **Lessons:** A theme is only as good as its least-covered color. Audit every hardcoded color when
  adding a second theme; one white value made the whole screen feel broken.

---

## BUG-021 — In-app packet search didn't scroll the PDF; commas blocked number matches
- **Date:** 2026-07-02
- **Severity:** Low (UX)
- **Status:** ✅ **Resolved 2026-07-02** (page-jump + comma-insensitive), then superseded by the PDF.js
  viewer which highlights and scrolls to the exact match.
- **Symptom:** (a) Searching a term found the match and named its page, but the embedded PDF stayed on
  page 1. (b) Searching `1748` did not find `1,748`.
- **Root cause:** (a) Setting the iframe `src` to a **fragment-only** change (`#page=N`) is ignored by
  the browser's sealed PDF viewer — no reload, no scroll. (b) The match compared raw strings, so a
  thousands-comma broke numeric matches.
- **Fix:** (a) First a nonce query to force real navigation (page jump); then the **PDF.js upgrade**
  (`ui/vendor/pdf.min.mjs`) which renders pages to high-DPI canvas with a highlight overlay — it now
  outlines every match and smooth-scrolls to the current one, with the browser viewer kept as a
  graceful fallback. (b) `normSearch()` strips commas from both query and text before matching.
- **Lessons:** The browser's built-in PDF viewer is a black box — no programmatic scroll/highlight.
  For controllable search UX you must render the PDF yourself (PDF.js). Normalize separators before
  matching money.

---

## BUG-022 — Nashville R&M mapping pointed at a sandbox-only account (screening stopped on production)
- **Date:** 2026-07-03
- **Severity:** Medium (blocked screening on production; no data impact — the safety gate did its job)
- **Status:** ✅ **Resolved 2026-07-03** — Nashville profile remapped to the real account; verified all
  profiles resolve against the live production chart.
- **Symptom:** Screening a Nashville packet ($2,485.91) on **production** stopped before planning with:
  *"QuickBooks accounts from config could not be resolved … Repairs and maintenance (for Repairs &
  Maintenance): Expected one QuickBooks account named 'Repairs and maintenance', but found 0."*
  Totals matched and all 12 lines were high-confidence — only the account lookup failed.
- **How we found it:** The new **report-ALL-missing-accounts** screening (BUG-012 follow-up) surfaced the
  exact account + category. A read-only query of the live chart confirmed the name didn't exist there.
- **Root cause:** The `500 Oak Street` (Nashville) profile mapped `Repairs & Maintenance` →
  `"Repairs and maintenance"` (spelled-out "and"). Production has only `"Repairs & maintenance"`
  (ampersand). The spelled-out account existed **only in the sandbox** as a testing duplicate — so the
  mapping resolved in sandbox but not production (**sandbox/production chart divergence**). The Keyrenter
  profile already used the correct ampersand form.
- **Fix:** Changed the Nashville mapping (and the `config.example.json` template) to
  `"Repairs & maintenance"`. Verified read-only against live production: all 8 Nashville accounts and all
  26 Keyrenter accounts now resolve. Config-only change — nothing posted to QuickBooks.
- **Prevention:** When switching environments, don't trust a sandbox chart dump as ground truth — verify
  config account names against the **target** company. The report-all-missing screening makes any such gap
  visible at screening time in one pass. Consider a `verify-config-accounts` command that resolves every
  profile's mapping against the connected company before go-live.
- **Lessons:** A sandbox that has *drifted* from production (extra/duplicate accounts) can hide real
  mapping errors. "Resolves in sandbox" ≠ "resolves in production." The screening gate correctly refused
  to guess an account — exactly the behavior we want before touching real books.
