---
name: tech-debt-static-analysis
description: Run the repo-local Tech Debt Harness (`scripts/debt_scan.py`), interpret `.debt/latest_summary.md`, and turn findings into a paydown plan. Use when the task involves static analysis, tech-debt quantification, lint/type/security triage, baseline/gate decisions, or deciding whether to accept new debt.
---

# Tech Debt Static Analysis

Use this skill to run and interpret the Tech Debt Harness in a code repository.

## When to use

Use when the task involves:

- static analysis;
- technical debt quantification;
- lint/type/security triage;
- creating a paydown plan;
- deciding whether to accept or block new debt.

## Steps

1. Run `python3 scripts/debt_scan.py doctor` to verify tools and language detection.
2. Run `python3 scripts/debt_scan.py scan`.
3. Read `.debt/latest_summary.md`.
4. For PR/CI work, run `python3 scripts/debt_scan.py compare` after a baseline exists.
5. Prioritize by monthly interest, then severity, then churn.
6. Do not refresh the baseline unless the debt is intentionally accepted.
7. When accepting debt, add an entry to `docs/tech-debt-tracker.md` with owner, expiry, and repayment plan.

## Interpretation rules

- Treat high/critical new findings as blockers unless the user explicitly accepts the risk.
- Treat high-churn files as higher exposure even when raw severity is medium.
- Prefer rules/tests over repeated manual review comments.
- Prefer small paydown PRs over broad rewrites.
- Always separate true positives, false positives, and intentional tradeoffs.

## Producing a written review

When the user asks for a written tech-debt review (not just a scan run), use
the structured output template at `docs/tech-debt-review-template.md` —
specifically its "Procedure" steps and the markdown table layout under
"Output format". Keep sections in the order shown there: Summary → Top
paydown opportunities → New debt → Automation opportunities.

## Related files

- `docs/tech-debt-policy.md` — severity and paydown policy.
- `docs/tech-debt-tracker.md` — human-facing ledger for accepted debt.
- `docs/tech-debt-review-template.md` — output template used by this skill.
- `.debt-harness.json` — tool config, severity model, and CI gates.
