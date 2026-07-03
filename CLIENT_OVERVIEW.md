# Audit Trail — Overview

*A plain-English summary of what this software does and why it can be trusted with your books.*

## The problem it solves
Every period, a property-management owner statement arrives as a single lump-sum deposit
in QuickBooks. Someone has to break that lump sum into dozens of correctly-categorized
lines — rent income, management fees, repairs, utilities, transfers, and more — for each
property. Done by hand, it's slow and error-prone.

## What this software does
It reads the owner-statement PDF, matches it to the right QuickBooks deposit, and prepares
the full categorized split automatically — then posts it to QuickBooks **only after a
human approves it**.

In short: **it does the tedious work, but never the guessing.**

## Why it can be trusted
The whole system is built around *not* making silent mistakes. Before anything is posted:

1. **It checks the math.** If the statement's numbers don't reconcile, it stops.
2. **It matches the exact deposit.** It won't act on the wrong transaction.
3. **It applies learned accounting rules** from how these books were handled historically.
4. **It explains every line** and flags anything ambiguous for a person to review.
5. **It blocks anything uncertain** instead of guessing.
6. **A human approves** the result before it posts.
7. **It double-checks its own work** after posting, and keeps an audit backup of every change.

> If the software isn't sure, it stops and asks. That is by design.

## Where the project is today
- The full pipeline is built and working.
- It's being validated end-to-end in a **QuickBooks sandbox** (a safe test company —
  no real books are touched) before any live use.
- Multiple real statements have already been split correctly in the sandbox.

## What's coming
- Finish sandbox validation and broaden automated testing.
- A simple **review-and-approve dashboard** so the whole process is point-and-click.
- Then, careful rollout to live books.

*For the technical details, see [README.md](README.md), [ARCHITECTURE.md](ARCHITECTURE.md),
and [ROADMAP.md](ROADMAP.md).*
