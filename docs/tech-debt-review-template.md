# Tech Debt Review — output template

Reference output template used by the `tech-debt-static-analysis` skill
(`.claude/skills/tech-debt-static-analysis/SKILL.md`) when producing a
human-facing review report. This is **not** a Claude skill itself — it is a
markdown reference loaded by that skill.

## Inputs

- `.debt/latest_summary.md`
- `.debt/latest_items.json`
- `.debt/baseline.json`, if present
- `docs/tech-debt-tracker.md`
- recent PR or Git diff, if available

## Procedure

1. Identify new high/critical findings first.
2. Sort remaining issues by `monthly_interest_minutes`, not by raw count.
3. Group related findings by file, subsystem, or repeated rule.
4. Distinguish false positives, intentional debt, and accidental debt.
5. For repeated findings, propose a stronger automated guardrail: lint rule, Semgrep rule, type-level constraint, test, or CI gate.
6. Recommend the smallest refactor that reduces the most monthly interest.
7. Update `docs/tech-debt-tracker.md` for accepted debt.

## Output format

Use this structure:

```markdown
## Summary
- Current principal:
- Current monthly interest:
- Gate status:

## Top paydown opportunities
| Rank | Area | Interest | Principal | Why now | Proposed fix |
|---|---|---:|---:|---|---|

## New debt
| Tool | Rule | File | Severity | Action |
|---|---|---|---|---|

## Automation opportunities
| Recurring issue | Better guardrail | Owner |
|---|---|---|
```
