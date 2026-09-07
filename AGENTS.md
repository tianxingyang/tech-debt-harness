# Agent instructions: Tech Debt Harness

When working in this repository, treat static-analysis findings as engineering facts, not style noise.

Before modifying code:

1. Read `.debt-harness.json` and `docs/tech-debt-policy.md`.
2. Check `.debt/latest_summary.md` if it exists.
3. Prefer localized fixes that reduce monthly interest without broad rewrites.
4. If a recurring review comment can be encoded as a lint, test, Semgrep rule, or architecture check, propose that instead of repeating the comment manually.

After modifying code:

```bash
python3 scripts/debt_scan.py scan
```

If the change intentionally accepts debt, add or update an entry in `docs/tech-debt-tracker.md` with owner, expected interest, and repayment plan.
