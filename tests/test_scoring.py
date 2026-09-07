"""Tests for severity normalization, debt estimation, fingerprinting, aggregation."""
from __future__ import annotations

import unittest
from pathlib import Path

from _harness import debt_scan


class TestNormalizeSeverity(unittest.TestCase):
    def test_eslint_levels(self) -> None:
        ns = debt_scan.normalize_severity
        self.assertEqual(ns("eslint", "2"), "high")
        self.assertEqual(ns("eslint", "error"), "high")
        self.assertEqual(ns("eslint", "1"), "medium")
        self.assertEqual(ns("eslint", "warn"), "medium")
        self.assertEqual(ns("eslint", "0"), "info")

    def test_ruff_rule_families(self) -> None:
        ns = debt_scan.normalize_severity
        self.assertEqual(ns("ruff", "F401"), "high")
        self.assertEqual(ns("ruff", "E9XX"), "high")
        self.assertEqual(ns("ruff", "S101"), "medium")
        self.assertEqual(ns("ruff", "I001"), "low")

    def test_bandit_levels(self) -> None:
        ns = debt_scan.normalize_severity
        self.assertEqual(ns("bandit", "HIGH"), "high")
        self.assertEqual(ns("bandit", "MEDIUM"), "medium")
        self.assertEqual(ns("bandit", "LOW"), "low")

    def test_cppcheck_levels(self) -> None:
        ns = debt_scan.normalize_severity
        self.assertEqual(ns("cppcheck", "error"), "high")
        self.assertEqual(ns("cppcheck", "warning"), "medium")
        self.assertEqual(ns("cppcheck", "style"), "low")
        self.assertEqual(ns("cppcheck", "information"), "info")

    def test_cargo_audit_levels(self) -> None:
        ns = debt_scan.normalize_severity
        self.assertEqual(ns("cargo_audit", "critical"), "critical")
        self.assertEqual(ns("cargo_audit", "high"), "high")
        # Any other shape is treated as high — conservative for deps.
        self.assertEqual(ns("cargo_audit", "low"), "high")

    def test_unknown_tool_defaults_to_medium(self) -> None:
        self.assertEqual(debt_scan.normalize_severity("unknown_tool", "whatever"), "medium")


class TestEstimateDebt(unittest.TestCase):
    def _cfg(self) -> dict:
        return {
            "severity_principal_minutes": {"critical": 120, "high": 60, "medium": 30, "low": 12, "info": 5},
            "severity_monthly_interest_rate": {"critical": 0.35, "high": 0.25, "medium": 0.12, "low": 0.05, "info": 0.02},
            "tools": {"eslint": {"principal_multiplier": 1.0}, "bandit": {"principal_multiplier": 1.5}},
        }

    def _issue(self, **kw) -> "debt_scan.Issue":
        defaults = dict(
            tool="eslint", language="typescript", rule_id="rule",
            severity="high", message="msg", file="src/a.ts", line=1,
        )
        defaults.update(kw)
        return debt_scan.Issue(**defaults)

    def test_principal_uses_severity_and_multiplier(self) -> None:
        issue = self._issue(tool="eslint", severity="high")
        out = debt_scan.estimate_debt(issue, self._cfg(), churn_by_file={})
        # 60 * 1.0 = 60
        self.assertEqual(out.principal_minutes, 60)

    def test_security_tool_high_gets_bumped(self) -> None:
        issue = self._issue(tool="bandit", severity="high")
        out = debt_scan.estimate_debt(issue, self._cfg(), churn_by_file={})
        # 60 * 1.5 * 1.2 = 108
        self.assertEqual(out.principal_minutes, 108)

    def test_churn_increases_monthly_interest(self) -> None:
        cfg = self._cfg()
        cold = debt_scan.estimate_debt(self._issue(file="cold.ts"), cfg, churn_by_file={})
        hot = debt_scan.estimate_debt(self._issue(file="hot.ts"), cfg, churn_by_file={"hot.ts": 16})
        self.assertGreater(hot.monthly_interest_minutes, cold.monthly_interest_minutes)
        # exposure_factor capped at 3.0 (1 + min(churn/8, 2))
        self.assertEqual(hot.churn_90d, 16)

    def test_fingerprint_is_stable_and_changes_with_location(self) -> None:
        cfg = self._cfg()
        a = debt_scan.estimate_debt(self._issue(file="src/a.ts", line=1), cfg, {})
        b = debt_scan.estimate_debt(self._issue(file="src/a.ts", line=1), cfg, {})
        c = debt_scan.estimate_debt(self._issue(file="src/a.ts", line=2), cfg, {})
        self.assertEqual(a.fingerprint, b.fingerprint)
        self.assertNotEqual(a.fingerprint, c.fingerprint)
        self.assertEqual(len(a.fingerprint), 20)


class TestAggregate(unittest.TestCase):
    def test_totals_and_top_files(self) -> None:
        issues = [
            debt_scan.Issue(tool="ruff", language="python", rule_id="F401", severity="high",
                            message="m", file="a.py", principal_minutes=10, monthly_interest_minutes=5),
            debt_scan.Issue(tool="ruff", language="python", rule_id="E501", severity="low",
                            message="m", file="a.py", principal_minutes=2, monthly_interest_minutes=1),
            debt_scan.Issue(tool="eslint", language="typescript", rule_id="x", severity="medium",
                            message="m", file="b.ts", principal_minutes=8, monthly_interest_minutes=3),
        ]
        agg = debt_scan.aggregate(issues)
        self.assertEqual(agg["issue_count"], 3)
        self.assertEqual(agg["principal_minutes"], 20)
        self.assertEqual(agg["monthly_interest_minutes"], 9)
        self.assertEqual(agg["by_tool"], {"ruff": 2, "eslint": 1})
        top_files = {f["file"]: f["monthly_interest_minutes"] for f in agg["top_files_by_interest"]}
        self.assertEqual(top_files["a.py"], 6)
        self.assertEqual(top_files["b.ts"], 3)


class TestRelpath(unittest.TestCase):
    def test_relative_inside_root_uses_posix(self) -> None:
        root = Path(__file__).resolve().parent.parent
        absolute = root / "scripts" / "debt_scan.py"
        rel = debt_scan.relpath(str(absolute), root)
        self.assertEqual(rel, "scripts/debt_scan.py")

    def test_empty_path_returns_empty(self) -> None:
        self.assertEqual(debt_scan.relpath("", Path.cwd()), "")


if __name__ == "__main__":
    unittest.main()
