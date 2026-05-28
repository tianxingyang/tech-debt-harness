# Technical Debt Tracker

Generated scanner output lives in `.debt/latest_summary.md`. This document is for intentional, human-reviewed debt.

## Dashboard

| Metric | Current | Target | Notes |
|---|---:|---:|---|
| Principal hours | TBD | trending down | from `.debt/latest_summary.md` |
| Monthly interest hours | TBD | trending down | from `.debt/latest_summary.md` |
| New high/critical findings | 0 | 0 | CI gate |
| Architecture/policy suppressions | TBD | reviewed weekly | keep scoped |

## Intentional debt items

| ID | Area | Type | Principal | Monthly interest | Owner | Accepted until | Repayment plan | Evidence |
|---|---|---|---:|---:|---|---|---|---|
| TD-0001 | example/module | architecture | 8h | 3h/mo | @owner | 2026-07-01 | Replace duplicate validators with canonical API | link PR / issue |

## Candidate paydown queue

Copy the top recurring findings from `.debt/latest_summary.md` here when they need human decision-making.

| Candidate | Why it matters | Proposed action | Decision |
|---|---|---|---|
|  |  |  |  |

## Suppression register

| Date | Tool | Rule | Scope | Reason | Owner | Revisit date |
|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |
