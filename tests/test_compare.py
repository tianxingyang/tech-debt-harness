"""Tests for baseline / compare gating logic."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _harness import debt_scan


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class TestCompareGates(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cfg = {
            "output_dir": ".debt",
            "gates": {
                "max_new_critical": 0,
                "max_new_high": 0,
                "max_monthly_interest_delta_minutes": 60,
                "max_total_issue_delta": 25,
            },
        }
        self.out = self.root / ".debt"
        self.out.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_baseline_passes_with_message(self) -> None:
        _write_json(self.out / "latest_items.json", {"summary": {"issue_count": 0, "monthly_interest_minutes": 0}, "issues": []})
        ok, msg = debt_scan.compare(self.root, self.cfg)
        self.assertTrue(ok)
        self.assertIn("No baseline", msg)

    def test_passes_when_no_new_high_findings(self) -> None:
        base_issues = [{"fingerprint": "fp1", "severity": "high"}]
        _write_json(self.out / "baseline.json", {
            "fingerprints": ["fp1"],
            "summary": {"issue_count": 1, "monthly_interest_minutes": 30},
            "issues": base_issues,
        })
        _write_json(self.out / "latest_items.json", {
            "summary": {"issue_count": 1, "monthly_interest_minutes": 30},
            "issues": base_issues,
        })
        ok, msg = debt_scan.compare(self.root, self.cfg)
        self.assertTrue(ok, msg)

    def test_fails_on_new_high(self) -> None:
        _write_json(self.out / "baseline.json", {
            "fingerprints": ["old"],
            "summary": {"issue_count": 0, "monthly_interest_minutes": 0},
            "issues": [],
        })
        _write_json(self.out / "latest_items.json", {
            "summary": {"issue_count": 1, "monthly_interest_minutes": 50},
            "issues": [{"fingerprint": "new1", "severity": "high"}],
        })
        ok, msg = debt_scan.compare(self.root, self.cfg)
        self.assertFalse(ok)
        self.assertIn("new high issues", msg)

    def test_fails_on_interest_delta(self) -> None:
        _write_json(self.out / "baseline.json", {
            "fingerprints": ["fp1"],
            "summary": {"issue_count": 1, "monthly_interest_minutes": 10},
            "issues": [{"fingerprint": "fp1", "severity": "low"}],
        })
        _write_json(self.out / "latest_items.json", {
            "summary": {"issue_count": 1, "monthly_interest_minutes": 200},
            "issues": [{"fingerprint": "fp1", "severity": "low"}],
        })
        ok, msg = debt_scan.compare(self.root, self.cfg)
        self.assertFalse(ok)
        self.assertIn("monthly interest delta", msg)


if __name__ == "__main__":
    unittest.main()
