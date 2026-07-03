# Engineering Principles

The engineering standard for this project. Every change — code, configuration, or
documentation — is expected to uphold these principles. They exist to keep a
production-oriented **accounting** automation platform trustworthy.

> Companion documents: the product/role charter is [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md),
> the system design is [ARCHITECTURE.md](ARCHITECTURE.md), and the long-term plan is
> [ROADMAP.md](ROADMAP.md). This file is the *why behind how we build*.

One sentence captures the philosophy:

> **Automate the work, never automate the mistakes.**

---

## Core principles

### 1. Correctness over automation
Producing the *right* accounting result matters more than producing a result quickly
or automatically. A feature that is fast but occasionally wrong is a liability, not an
asset. When in doubt, do less, correctly.

### 2. Safety before speed
Performance and convenience never justify weakening a safety check. The screening gate,
reconciliation checks, and approval workflow are load-bearing — optimize around them,
never through them.

### 3. Sandbox before Production
All work targets the QuickBooks **sandbox** until explicitly and deliberately promoted.
No production change happens without a conscious decision and confirmation. Sandbox and
production are separate companies with separate tokens.

### 4. Historical accounting rules override keyword matching
The expert-history layer (learned from real, expert-posted QuickBooks splits) takes
precedence over naive PDF keyword matching. Historical truth beats text that merely
looks plausible.

### 5. Every automated decision must be explainable
For any categorization, correction, or block, the system must be able to say *why* in
human-readable terms. If a decision can't be explained, it shouldn't be made
automatically. This is what makes the output defensible to an accountant or auditor.

### 6. Human approval before QuickBooks modification
Nothing is posted to the books without passing screening and, where warnings exist, an
explicit approval that is fingerprinted to the exact reviewed result. The software is a
junior accountant that never posts without sign-off.

### 7. Never silently modify accounting data
The system must never quietly change, drop, or "fix" a line to make things balance. If
data doesn't reconcile, it stops and surfaces the problem — it does not paper over it.

### 8. Prefer stopping over guessing
When inputs are ambiguous, duplicated, or mixed, the system blocks and asks for review
rather than picking a best-guess. Blocking is a feature, not a failure.

### 9. Prefer explicit validation over assumptions
Validate that accounts and customers exist, that totals reconcile, and that the right
deposit was matched — don't assume. Make preconditions checks, not hopes.

### 10. Every change should be testable
New behavior should come with (or enable) a test. The regression suite is what lets the
project evolve without fear of breaking a verified result. Untestable logic is a
maintainability risk.

### 11. Every change should be reversible
Favor designs that can be undone: dry-run previews before writes, audit backups before
applying, and version control with clean history. Avoid one-way doors.

### 12. Preserve reconciliation and audit history
Reconciliation math and the audit trail (`runtime/audit/`) are part of the product's
value. Never remove or bypass them for convenience.

### 13. Favor maintainability over clever code
Clear, boring, well-named code that the next person (or future you) can read beats
clever code that saves a few lines. Preserve the modular architecture; refactor over
rewrite.

### 14. Documentation is part of the product
Docs are maintained with the same care as code. When behavior changes, the relevant
docs (`README.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, this file) change in the same
breath. Out-of-date documentation is a defect.

### 15. Protect secrets and client data
Credentials and real client data never enter version control. `.env*`, `secrets/`,
`config.json`, `runtime/`, and real statement PDFs stay local and gitignored.

---

## How these principles show up in the system
| Principle | Where it lives in the code/process |
|---|---|
| Correctness / stop over guess | Parser math reconciliation; screening `blocked` status |
| Safety before speed | `app/screening.py` gate on every `split` |
| Human approval | `app/approvals.py` fingerprinted approval files |
| Explainable decisions | Expert-rule `changes`/`warnings`; screening `reasons` |
| Reversible | Dry-run default; `runtime/audit/` backups before apply |
| Sandbox first | `QBO_ENVIRONMENT` in `.env`; separate tokens |
| Testable | `tests/` suite (`python -m unittest discover -s tests -v`) |

---

## Applying these in practice
- Before building a feature: confirm it doesn't weaken a safety guarantee. If it might,
  stop and discuss the trade-off first.
- Before merging: tests pass, docs updated, no secrets staged.
- When unsure whether to automate something: ask *"would a professional accountant
  approve this without checking?"* If not, require review.

These principles are deliberately stable. Changing one is an architectural decision and
should be recorded (see [CHANGELOG.md](CHANGELOG.md)).
