# Client Demo Runbook — Sandbox Split (Deposits 150 & 151)

For the **Friday, 2026-07-03** client demonstration. This walks through splitting two
owner-statement deposits live in the QuickBooks **sandbox**, showing the safety-first
workflow. Follow it top to bottom.

> Golden rule on screen: this is the **sandbox** company. Nothing here touches real books.

---

## A. Before the client joins (5-minute pre-flight)

```powershell
# 1. Get the latest committed code
git pull

# 2. Open the project in VS Code, open a terminal, activate the environment
.\.venv\Scripts\Activate.ps1     # prompt should show (.venv)

# 3. Confirm SANDBOX
type .env | Select-String QBO_ENVIRONMENT     # must read: sandbox

# 4. Sanity-check the test suite is green
python -m unittest discover -s tests -v        # expect 9 OK

# 5. Confirm both deposits exist as a single unsplit line (read-only)
python main.py qbo-deposit --deposit-id 150 --raw    # total 4689.89, line_count 1
python main.py qbo-deposit --deposit-id 151 --raw    # total 3604.60, line_count 1
```

If anything above looks off, fix it **before** the demo (see Troubleshooting).

---

## B. The story to tell (30 seconds)

> "This software reads a property-management owner statement, figures out the correct
> accounting split, and posts it to QuickBooks — but it **refuses to post anything it
> isn't sure about**. It behaves like a junior accountant that never posts without my
> approval. Watch the safety checks."

Key beats to emphasize as you go: **dry-run first → it explains every line → it blocks
on anything uncertain → I approve → it posts → it verifies its own work.**

---

## C. Deposit 150 — live walkthrough (Aug 2025, $4,689.89)

### 1. Dry-run preview (writes nothing)
```powershell
python main.py --output runtime\preview-150.json split --deposit-id 150 --pdf "D:\Project Automation\Quickbooks Automation Testing\Owner Statements 2021-2026\keyrenter history\Owner packet (36).pdf"
```
**Say:** "First, a dry run. It read the PDF, matched the deposit, and built the split —
but nothing was written yet." Point out in the output: `total_matches: true`, the
deposit total **$4,689.89**, and the planned line count.

### 2. Review what it flagged
**Say:** "It flagged a few lines for me to review rather than guessing." Walk through
the `reasons` / `expert_rule_warnings` — explain that warnings mean *review*, not error.

### 3. Approve (writes nothing)
```powershell
python main.py --output runtime\approval-150.json approve-screening --review-file runtime\preview-150.json --approved-by "Reviewer" --notes "Reviewed warnings; correct as labeled."
```
**Say:** "I reviewed and approved. The approval is fingerprinted to exactly what I saw —
if anything changed, it would be rejected automatically."

### 4. Apply to QuickBooks (the only step that writes)
```powershell
python main.py --output runtime\apply-150.json split --deposit-id 150 --pdf "D:\Project Automation\Quickbooks Automation Testing\Owner Statements 2021-2026\keyrenter history\Owner packet (36).pdf" --apply --approval-file runtime\approval-150.json
```
**Success =** `result` shows `"status": "updated"`, `"deposit_total": "4689.89"`, and the
expected line count. **Say:** "And it verified its own work — the posted total matches
the statement, or it would have stopped and told me."

### 5. Show it in QuickBooks
Open deposit 150 in the sandbox UI — show the clean split, total **$4,689.89**.

---

## D. Deposit 151 — repeat (Sep 2025, $3,604.60)

Same four steps, swapping `150 → 151` and the PDF to `Owner packet (37).pdf`:
```powershell
# Preview
python main.py --output runtime\preview-151.json split --deposit-id 151 --pdf "D:\Project Automation\Quickbooks Automation Testing\Owner Statements 2021-2026\keyrenter history\Owner packet (37).pdf"
# (review warnings)
# Approve
python main.py --output runtime\approval-151.json approve-screening --review-file runtime\preview-151.json --approved-by "Reviewer" --notes "Reviewed warnings; correct as labeled."
# Apply
python main.py --output runtime\apply-151.json split --deposit-id 151 --pdf "D:\Project Automation\Quickbooks Automation Testing\Owner Statements 2021-2026\keyrenter history\Owner packet (37).pdf" --apply --approval-file runtime\approval-151.json
```
**Success =** `"deposit_total": "3604.60"`.

---

## E. If something looks wrong (don't panic — the gates are doing their job)
- **`Apply verification FAILED…`** → the tool caught a mismatch and refused to post a
  wrong total. This is the safety net working. Stop, note it, and review after the demo
  (and log it in [BUG_LOG.md](BUG_LOG.md)).
- **Screening `blocked`** → it found something uncertain. Read the `reasons`; do not
  force it. "The software stopping is a feature" is a strong thing to show a client.
- **`ModuleNotFoundError` / `python` not found** → the venv isn't active. Re-run
  `.\.venv\Scripts\Activate.ps1` (note the leading `.`), or use
  `.\.venv\Scripts\python.exe` instead of `python`.
- **Wrong environment** → if `.env` doesn't say `sandbox`, stop and fix before applying.

---

## F. One-line recap for the client
> "Two real owner statements, split into correctly-categorized QuickBooks deposits, with
> a human approval and an automatic correctness check on every single one — and full
> audit backups kept for each."
