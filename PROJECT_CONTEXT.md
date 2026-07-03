# Audit Trail - Project Context

Last updated: 2026-06-28

## Project identity

This project is a production-oriented QuickBooks Online automation platform.

It is not just a PDF parser or a scripting project. The goal is to build reliable accounting software that automates owner-statement processing while maintaining accounting accuracy, safety, reviewability, and auditability.

The system should feel like an accounting review assistant, not simply an automation script.

## Long-term vision

The application should eventually become a desktop or web application where a user can:

- import or automatically receive owner statements,
- parse owner-statement PDFs,
- match statements to the correct QuickBooks deposits,
- generate proposed split transactions,
- compare proposed splits against historical QuickBooks data,
- detect inconsistencies and accounting anomalies,
- generate correction previews showing proposed changes,
- display confidence scores and explanations for every correction,
- allow the user to approve or reject changes,
- apply approved changes to QuickBooks Online,
- maintain a permanent audit trail.

## Development philosophy

Correctness is always more important than automation.

The software should never blindly modify accounting records.

If confidence is low or reconciliation fails, the system should stop, explain why, and require human review.

The objective is to automate routine, well-understood cases while protecting users from incorrect accounting changes.

## Current architecture

The project is organized into modules with clear responsibilities:

- `parser.py` reads owner statements and extracts structured transaction data.
- `history.py` uses historical statements and previous QuickBooks data for consistency checks.
- `expert_rules.py` contains accounting knowledge learned from historical QuickBooks entries.
- `screening.py` performs safety validation before any QuickBooks modification.
- `approvals.py` manages the approval workflow.
- `quickbooks.py` handles QuickBooks Online API communication.
- `split_audit.py` compares expected accounting against existing QuickBooks data.
- `service.py` coordinates the complete workflow.

## Core principles

Always preserve these principles:

- never blindly modify QuickBooks,
- always screen proposed changes before applying them,
- historical accounting knowledge overrides simple keyword matching,
- every correction should include a human-readable explanation,
- every run should produce a screening report,
- preserve audit history,
- preserve reconciliation,
- sandbox testing must pass before production,
- favor correctness over automation speed,
- prefer stopping over guessing.

## Coding standards

When modifying the project:

- preserve modular architecture,
- prefer refactoring over rewriting,
- avoid duplicated logic,
- keep functions focused and readable,
- use descriptive variable names,
- add docstrings for public functions,
- maintain backwards compatibility whenever practical,
- explain architectural changes before implementing them.

## Testing philosophy

Every change should consider:

- historical owner statements,
- historical QuickBooks entries,
- regression risk,
- accounting accuracy,
- edge cases.

Whenever practical, add or recommend regression tests before changing production behavior.

## Documentation rules

When significant changes are made:

- update `CHANGELOG.md`,
- update `README.md` if behavior changes,
- update `ARCHITECTURE.md` if architecture changes,
- keep documentation synchronized with implementation.

## Engineering role

This project is engineered with the mindset of a senior software engineer and technical reviewer.

Focus on improving:

- architecture,
- reliability,
- testing,
- maintainability,
- performance,
- user experience,
- documentation.

Do not optimize for writing code as quickly as possible.

Optimize for building software that can safely be used in a real accounting environment.

When multiple implementation approaches exist, explain the tradeoffs before recommending one.

Challenge design decisions when appropriate, while preserving the project safety philosophy.

