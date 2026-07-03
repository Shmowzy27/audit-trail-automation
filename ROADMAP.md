# Roadmap — Audit Trail

> **What this is:** the long-term plan for the project, kept in sync with the
> [Engineering Charter](PROJECT_CONTEXT.md).
> Last updated: 2026-06-28.

## Vision

Build accounting automation software that can be **trusted** with real books. It
should behave like a **junior accountant that never posts to the books without
human approval** — automating the tedious work while refusing to automate
uncertainty.

**Primary scope: make this work reliably for one client.** That is the whole
vision — getting it correct and trustworthy for the one client comes first.

The objective is **correctness and trust, not raw automation**. The guiding test
for every decision: *"Would a professional accountant feel comfortable approving
this transaction?"* If the answer is uncertain, the software **stops, explains,
and requires human review.**

### What the system does (the pipeline)
1. **Read** owner statements (Keyrenter, Nashville, and potentially others) — from Gmail or manual import.
2. **Parse** the PDF into structured accounting data.
3. **Match** the correct QuickBooks Online deposit.
4. **Learn** from historical accounting patterns.
5. **Plan** the correct split transactions.
6. **Explain** every decision; compare against prior months; detect inconsistencies.
7. **Screen** — block anything unsafe or uncertain.
8. **Approve** — present everything for human approval (audit trail recorded).
9. **Apply** — only then update QuickBooks.

### Design principle: never *silently* wrong
No parser is 100% accurate on arbitrary PDFs. This system does not need to be —
when it is unsure, it **blocks rather than guesses**. The honest, defensible goal
is *"never silently wrong,"* enforced by the screening + approval gates.

---

## Phases

### Phase 1 — Foundation ✅ (Complete)
- Project architecture established; modular Python codebase
- PDF parser
- QuickBooks Online integration
- Historical reconciliation engine
- Expert accounting rules
- Screening (safety) engine
- Approval workflow
- Audit engine
- Private GitHub repository
- Engineering docs (README, ARCHITECTURE, PROJECT_PROGRESS, Engineering Charter)

### Phase 2 — Stabilization 🔵 (**You are here**)
- Finish **sandbox testing** end-to-end (deposits 148–151, one at a time)
- Resolve **screening blockers** (e.g. missing chart-of-accounts mappings)
- **Expand regression tests** — capture known-good splits so future code changes are safe
- Improve **parser robustness**
- Clean repository structure
- Keep documentation synchronized

> **Lead's note:** Phase 2 is the most important and the easiest to under-invest in.
> A UI on top of an unreliable parser only makes wrong answers prettier. The
> regression-test suite (locking in correct splits for 148–151) is the real
> deliverable here — it's what lets us change code later without fear.

### Phase 3 — Desktop / Web Application
- Approval dashboard (Review / Approve / Reject workflow)
- Correction-preview UI
- Confidence indicators
- Audit-history viewer
- Settings page
- Sandbox / Production switch
- Goal: no command line for the end user

### Phase 4 — Production Readiness
- Installer
- Logging & diagnostics; error reporting
- Automated backups
- CI/CD
- Comprehensive test suite
- Production monitoring

