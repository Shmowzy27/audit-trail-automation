# Operations Runbook & Command Reference

The day-to-day operations guide for this project — for you now, and for any future
contributor. It covers **environment setup, running the system (the pipeline), tests,
Git, and recovery.** Every command entry says **what** it does, **how** it works, and
**when** to use it.

This is a production-oriented accounting automation platform. Treat the safety rules
below as non-negotiable, not suggestions.

> **Two safety rules that override everything else in this document:**
> 1. **Sandbox first.** Confirm `QBO_ENVIRONMENT=sandbox` in `.env` before any
>    QuickBooks command. Never run against Production without deciding to on purpose.
> 2. **Dry-run before apply.** Never run a `--apply` command until the same command
>    *without* `--apply` (the preview) has passed screening and you've reviewed it.

---

## Contents
1. [Quick Start (5 Minutes)](#1-quick-start-5-minutes)
2. [⛔ Never Do This](#2--never-do-this)
3. [Conventions](#3-conventions)
4. [Start of every working session](#4-start-of-every-working-session)
5. [One-time / occasional setup](#5-one-time--occasional-setup)
6. [Running the system — the safe pipeline](#6-running-the-system--the-safe-pipeline)
7. [Other read-only QuickBooks lookups](#7-other-read-only-quickbooks-lookups)
8. [Tests](#8-tests)
9. [Git — workflow & everyday loop](#9-git--workflow--everyday-loop)
10. [Emergency Recovery](#10-emergency-recovery)
11. [Current sandbox test set](#11-current-sandbox-test-set)
12. [Current Project Status](#12-current-project-status)

---

## 1. Quick Start (5 Minutes)

The normal daily workflow for processing one deposit, start to finish. Each step links
to fuller detail in [Section 6](#6-running-the-system--the-safe-pipeline). Replace
`<id>` and `<pdf-path>` with real values (see [Section 11](#11-current-sandbox-test-set)).

```powershell
# 1. Open VS Code in the project folder, then open a terminal.

# 2. Activate the virtual environment
.\.venv\Scripts\Activate.ps1

# 3. Confirm you're pointed at SANDBOX (must say sandbox)
type .env | Select-String QBO_ENVIRONMENT

# 4. Parse the PDF (no QuickBooks contact)
python main.py --output runtime\parse-<id>.json parse --pdf "<pdf-path>"

# 5. Dry-run preview — builds the split and runs screening (writes nothing)
python main.py --output runtime\preview-<id>.json split --deposit-id <id> --pdf "<pdf-path>"

# 6. Review screening results — open runtime\preview-<id>.json and check
#    "apply_allowed" and "reasons". If apply_allowed is false, STOP and resolve.

# 7. Approve (ONLY if screening raised warnings you've reviewed and judged correct)
python main.py --output runtime\approval-<id>.json approve-screening --review-file runtime\preview-<id>.json --approved-by "Reviewer" --notes "Reviewed; correct as labeled"

# 8. Apply the split to QuickBooks (sandbox) — the only step that writes
python main.py --output runtime\apply-<id>.json split --deposit-id <id> --pdf "<pdf-path>" --apply --approval-file runtime\approval-<id>.json

# 9. Verify — read the deposit back and confirm the split posted correctly
python main.py qbo-deposit --deposit-id <id> --raw
```

If anything looks wrong at step 6 or later, **do not** push forward — see
[Section 2](#2--never-do-this) and [Section 10](#10-emergency-recovery).

---

## 2. ⛔ Never Do This

These are the actions that break trust or risk the books. They are not negotiable.

- ⛔ **Never run `--apply` without a passing dry-run preview first.** The preview is
  your proof the split is correct and safe.
- ⛔ **Never bypass, disable, or weaken the screening engine.** If screening blocks,
  the answer is to fix the cause — not to route around the gate.
- ⛔ **Never point at Production by accident.** Confirm `QBO_ENVIRONMENT=sandbox` every
  session. Only switch to production as a deliberate, confirmed decision.
- ⛔ **Never manually edit a QuickBooks deposit when the automation reports a
  reconciliation problem.** Hand-fixing hides the real issue. Fix the data/parser/config
  and re-screen instead.
- ⛔ **Never silently "make the numbers match"** by editing amounts to clear a screening
  block. Investigate *why* they don't reconcile.
- ⛔ **Never commit secrets or local data.** These are gitignored — keep it that way:
  - `.env`, `.env.production`, `.env.sandbox`
  - `config.json`
  - `secrets/`
  - `runtime/`
  - real owner-statement PDFs / client data
- ⛔ **Never `git push --force` to shared history** without `--force-with-lease` and a
  clear reason. Prefer `git revert` for already-pushed mistakes.

---

## 3. Conventions

- Commands are run from the project root: `D:\Project Automation\qbo-owner-statement-automation`.
- `python` below means the **project's virtual-environment Python**. Activate it
  once per terminal ([Section 4](#4-start-of-every-working-session)) so `python` points at the right interpreter.
- Anything in `<angle brackets>` is a value you fill in (e.g. `<deposit-id>`).
- **Global flags `--config` and `--output` go BEFORE the subcommand.** This is the
  #1 easy mistake. Correct: `python main.py --output runtime\x.json qbo-accounts`.

---

## 4. Start of every working session

```powershell
# Activate the virtual environment (so `python` = the project's Python with all deps)
.\.venv\Scripts\Activate.ps1
```
- **What:** turns on the isolated Python environment for this project.
- **How:** prepends `.venv` to your PATH; your prompt shows `(.venv)`.
- **When:** first thing, every time you open a new terminal.
- **If it's blocked** by execution policy, either run
  `Set-ExecutionPolicy -Scope Process Bypass` once, or skip activation and use the
  full path `.\.venv\Scripts\python.exe` in place of `python` everywhere below.

```powershell
# Confirm which QuickBooks environment you're pointed at
type .env | Select-String QBO_ENVIRONMENT
```
- **What/why:** verifies you're in **sandbox** before doing anything that could
  touch the books. Do this every session as a habit.

---

## 5. One-time / occasional setup

| Command | What it does | When |
|---|---|---|
| `powershell -ExecutionPolicy Bypass -File .\setup.ps1` | Creates `.venv`, installs `requirements.txt`, copies `.env`/`config.json` from examples, makes `secrets/` + `runtime/`. | First time on a new machine, or after deleting `.venv`. (Same as VS Code task **"First-time setup"**.) |
| `python authorize_quickbooks.py` | Runs the QuickBooks OAuth flow and writes the token into `secrets/`. | First time, or when the QBO token expires / you switch sandbox⇄production. |
| `pip install -r requirements.txt` | (Re)installs dependencies into the active venv. | After `requirements.txt` changes. |

---

## 6. Running the system — the safe pipeline

This is the core repetitive workflow for processing one deposit. **Do the steps in
this order.** Steps 1–4 are read-only/safe; only step 6 changes QuickBooks.

### Step 1 — Read-only checks (no PDF needed)
```powershell
python main.py --output runtime\accounts.json qbo-accounts
python main.py qbo-deposit --deposit-id <deposit-id> --raw
```
- **What:** dump the chart of accounts; inspect one deposit as it currently is.
- **When:** before splitting, to confirm the accounts your `config.json` needs
  exist, and to see the deposit's current state. **Pure reads — never change QBO.**

### Step 2 — Parse the PDF (no QuickBooks contact)
```powershell
python main.py --output runtime\parse-<id>.json parse --pdf "<pdf-path>"
```
- **What:** turns the PDF into structured line items and checks it reconciles.
- **How:** runs only the parser; does **not** touch QuickBooks.
- **When:** first look at any statement — confirm it parsed correctly and the total
  matches before involving QBO.

### Step 3 — Dry-run the split (screening preview, SAFE)
```powershell
python main.py --output runtime\preview-<id>.json split --deposit-id <deposit-id> --pdf "<pdf-path>"
```
- **What:** builds the planned split and runs it through the **screening safety
  gate** — but because there's no `--apply`, **nothing is written.**
- **How:** if screening finds a problem (total mismatch, missing account, warning)
  it sets `apply_allowed: false` and explains why in the output file.
- **When:** always, before applying. This is your main diagnostic.

### Step 4 — (If the deposit is already inspected) compare against QBO
```powershell
python main.py --output runtime\audit-<id>.json audit-split --deposit-id <deposit-id> --pdf "<pdf-path>"
```
- **What:** compares the PDF-derived split vs. what's already in QuickBooks
  (amounts/accounts/customers). Read-only.
- **When:** when a deposit may already be split and you want to see differences.

### Step 5 — Approve warnings (only if screening flagged warnings)
```powershell
python main.py --output runtime\approval-<id>.json approve-screening --review-file runtime\preview-<id>.json --approved-by "Reviewer" --notes "Reviewed warnings; correct as labeled"
```
- **What:** turns a reviewed preview into an **approval file**. Does **not** contact
  QuickBooks.
- **How:** the approval is tied to that exact screening result — if the preview
  changes later, you must re-approve (this is a safety feature).
- **When:** only after you've read the warnings in the preview and judged them correct.

### Step 6 — APPLY the split (writes to QuickBooks) ⚠️
```powershell
python main.py --output runtime\apply-<id>.json split --deposit-id <deposit-id> --pdf "<pdf-path>" --apply --approval-file runtime\approval-<id>.json
```
- **What:** actually posts the split to QuickBooks (sandbox).
- **How:** re-runs screening; only writes if it passes and the approval matches.
- **When:** last step, after a clean preview (+ approval if there were warnings).
- Add `--allow-resplit` **only** if the deposit is already split and you intend to
  replace the existing split.

### Step 7 — Verify the result
```powershell
python main.py qbo-deposit --deposit-id <deposit-id> --raw
```
- **What/when:** read the deposit back to confirm the split posted as expected.

---

## 7. Other read-only QuickBooks lookups (safe anytime)

| Command | Use when |
|---|---|
| `python main.py qbo-accounts --keyword "Leasing"` | Find a specific account by keyword. |
| `python main.py qbo-customers --keyword "Keyrenter"` | Find a customer. |
| `python main.py qbo-deposits --start 2025-01-01 --end 2025-12-31 --memo-keyword Keyrenter` | List deposits in a date range / by memo. |
| `python main.py --output runtime\history.json history --folder "<folder>" --start-year 2025 --end-year 2025` | Audit many statement PDFs together (no QBO contact). |

---

## 8. Tests

```powershell
python -m unittest discover -s tests -v
```
- **What:** runs the whole test suite (currently 8 tests) with verbose output.
- **How:** discovers every `test_*.py` in `tests\`. (Same as VS Code task
  **"Run all tests"**.)
- **When:** **before and after** any code change, and before committing. If a test
  fails, stop and fix it before moving on — green tests are what let you change
  code without fear (Phase 2 goal in [ROADMAP.md](ROADMAP.md)).

```powershell
python -m unittest tests.test_parser -v
```
- **What/when:** run just one test module while working on that area (faster).

---

## 9. Git — workflow & everyday loop

### The normal development cycle
```text
Start work
   │
   ▼
git pull                ← get the latest from GitHub before you change anything
   │
   ▼
Implement feature / make changes
   │
   ▼
Run tests               ← python -m unittest discover -s tests -v
   │
   ▼
git status              ← see exactly what changed
   │
   ▼
git add <files>         ← stage the changes you reviewed
   │
   ▼
git commit -m "..."     ← snapshot locally with a clear, descriptive message
   │
   ▼
git push                ← publish to GitHub
```

**Why each step exists:** `git pull` avoids diverging from the remote · running tests
first proves you didn't break anything · `git status` lets you review before committing ·
`add`/`commit` create a deliberate, reviewable history · `push` backs the work up and
publishes it.

> **Every completed feature should end with: ✅ passing tests · ✅ a descriptive
> commit message (explain *what & why*; prefix docs with `Docs:`) · ✅ a push to GitHub.**
> (Solo project — no co-author trailers.)

### The everyday commands
```powershell
git status                 # See what changed / what's staged. Run this constantly.
git diff                   # See the exact line-by-line changes (unstaged).
git add <file>             # Stage a specific file for the next commit.
git add -A                 # Stage ALL changes (use deliberately).
git commit -m "Subject"    # Save a snapshot locally with a message.
git push                   # Upload local commits to GitHub (origin/main).
```

- **`git status`** — *what:* shows changed, staged, and untracked files. *When:*
  before and after every other git command, until it's second nature.
- **`git diff`** — *what:* the actual changes. *When:* before staging, to review
  what you're about to commit.
- **`git add`** — *what:* stages changes (chooses what goes in the commit). *When:*
  once you've reviewed and want to include a file.
- **`git commit -m "..."`** — *what:* permanent local snapshot. *When:* after
  staging a logical unit of work. Write messages that explain *what & why*; prefix
  docs with `Docs:`. (Solo project — no co-author trailers.)
- **`git push`** — *what:* sends commits to GitHub. *When:* after committing, to
  back up and publish.

### Safety habit before pushing
Run `git status` and make sure **no secrets are staged** — `.env*`, `secrets/`,
`config.json`, and `runtime/` are gitignored, but always glance at the list. Only
`.example` templates should ever be committed.

---

## 10. Emergency Recovery

When something goes wrong, **stop and look before acting.** Most mistakes are
recoverable. Commands are ordered safest → most destructive.

```powershell
git status                 # Always start here — understand the current state.
git diff                   # See unstaged changes you might want to keep or discard.
git diff --staged          # See what's staged for the next commit.
git log --oneline -10      # Recent history, compact — find a commit to return to.
git reflog                 # EVERY position HEAD has been at — your ultimate undo map.
```
- **`git status` / `git diff`** — diagnose before you change anything.
- **`git log --oneline` / `git reflog`** — find the commit hash you want to recover to.
  `reflog` even shows states that `log` no longer does (e.g. after a reset).

```powershell
git restore <file>             # Discard UNSTAGED changes to a file (cannot be undone).
git restore --staged <file>    # Unstage a file but KEEP your edits.
git checkout <commit> -- <file># Restore one file to how it was at <commit>.
```
- Use these for "I messed up one file" situations. `git restore --staged` is safe (keeps
  your work); `git restore <file>` throws away uncommitted edits — be sure first.

```powershell
git revert <commit>            # Make a NEW commit that undoes <commit>. Safe for pushed history.
```
- **When:** you need to undo a commit that's **already on GitHub**. This is the safe way —
  it doesn't rewrite history.

```powershell
git reset --hard <commit>      # ⚠️ DESTRUCTIVE: move branch to <commit>, discard everything after.
```
- **When:** local-only cleanup, e.g. abandon a broken local rewrite. **Only safe before
  pushing.** It discards uncommitted changes and commits after `<commit>`. Pair it with
  `git reflog` first so you know the hash to return to.

### Environment / app recovery
```powershell
python authorize_quickbooks.py                       # Re-auth if QBO tokens expired or 401s appear.
powershell -ExecutionPolicy Bypass -File .\setup.ps1 # Rebuild a broken venv / missing deps.
```
- Deleting a bad file under `runtime\` is always safe — that folder is gitignored,
  disposable output.

---

## 11. Current sandbox test set (concrete examples)

Deposits being validated in Phase 2. PDFs live in
`D:\Project Automation\Quickbooks Automation Testing\Owner Statements 2021-2026\keyrenter history\`.

| Deposit | Month | Amount | PDF |
|---|---|---|---|
| 148 | Mar 2025 | $4,496.33 | `Owner packet (31).pdf` |
| 149 | Jun 2025 | $6,273.93 | `Owner packet (34).pdf` |
| 150 | Aug 2025 | $4,689.89 | `Owner packet (36).pdf` |
| 151 | Sep 2025 | $3,604.60 | `Owner packet (37).pdf` |

Example — full dry-run preview for deposit 148:
```powershell
python main.py --output runtime\preview-148.json split --deposit-id 148 --pdf "D:\Project Automation\Quickbooks Automation Testing\Owner Statements 2021-2026\keyrenter history\Owner packet (31).pdf"
```

---

## 12. Current Project Status

A snapshot of where the platform is. Full detail and phases live in [ROADMAP.md](ROADMAP.md);
deferred small items live in [REVIEW_LATER.md](REVIEW_LATER.md).

**Completed (Phase 1 — Foundation):**
- PDF parsing
- QuickBooks Online integration
- Historical validation
- Expert accounting rules
- Screening engine
- Approval workflow
- Audit trail
- GitHub integration
- Documentation system

**Current focus (Phase 2 — Stabilization):**
- Sandbox validation (deposits 148–151)
- Regression testing
- Parser improvements
- Confidence scoring
- Approval dashboard groundwork

**Future:**
- Desktop / web application (Phase 3)
- Production readiness (Phase 4)
