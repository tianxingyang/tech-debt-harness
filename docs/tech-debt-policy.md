# Technical Debt Policy

This repository treats technical debt as a recurring cost, not merely as messy code.

## Definitions

- **Principal**: estimated one-time effort to fix the finding.
- **Monthly interest**: estimated recurring cost caused by leaving it unfixed.
- **Exposure**: how often the affected file or area changes.
- **Intentional debt**: accepted debt with owner, expiry, and repayment plan.
- **Accidental debt**: debt introduced without an explicit decision.

## Severity policy

| Severity | Meaning | Default action |
|---|---|---|
| Critical | likely security/data-loss/correctness failure | block new code unless explicitly waived |
| High | type/correctness/security issue or serious maintainability risk | block new high findings |
| Medium | maintainability, performance, portability, or moderate risk | allow only if trend is stable or improving |
| Low | style, small cleanup, low-risk refactor | batch and fix opportunistically |
| Info | weak signal, configuration issue, tool note | inspect only if repeated |

## Baseline policy

A baseline is not a quality certificate. It is a line in the sand.

Refresh `.debt/baseline.json` only when one of the following is true:

1. A debt item is intentionally accepted and documented in `docs/tech-debt-tracker.md`.
2. A tool version changed and the delta is reviewed.
3. A large migration temporarily increases debt and has a dated repayment plan.

Do not refresh the baseline simply to make CI pass.

## Suppression policy

Suppression is acceptable when the issue is a false positive or an intentional tradeoff. A suppression should be scoped as narrowly as possible and include a reason.

Preferred order:

1. Fix the issue.
2. Refactor to make the rule pass naturally.
3. Add a narrow local suppression with a comment.
4. Add a tool-level suppression only when local suppression is impossible.

## Paydown priority

Prioritize in this order:

1. New critical/high findings.
2. High monthly-interest findings in high-churn files.
3. Findings that encode recurring review comments.
4. Findings that block agent or human ability to verify changes.
5. Low-interest cleanup.

## Weekly review ritual

Run:

```bash
python3 scripts/debt_scan.py scan
```

Then review:

- total monthly interest trend;
- new critical/high findings;
- top files by monthly interest;
- suppressions added this week;
- recurring findings that should become a stronger rule, test, or architecture check.
