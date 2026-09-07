"""Fixture-based tests for each analyzer output parser in debt_scan."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from _harness import debt_scan

# Use a stable fake repo root so the relpath logic produces predictable values
# without touching the real filesystem.
FAKE_ROOT = Path("/fake/repo").resolve() if Path("/").exists() else Path("C:/fake/repo")


class TestParseEslint(unittest.TestCase):
    def test_parses_messages_and_normalizes_severity(self) -> None:
        payload = json.dumps([
            {
                "filePath": str(FAKE_ROOT / "src/a.ts"),
                "messages": [
                    {"ruleId": "no-floating-promises", "severity": 2, "message": "Bad", "line": 10, "column": 3},
                    {"ruleId": "no-explicit-any", "severity": 1, "message": "Warn", "line": 12, "column": 1},
                ],
            }
        ])
        issues = debt_scan.parse_eslint(payload, FAKE_ROOT)
        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0].tool, "eslint")
        self.assertEqual(issues[0].severity, "high")
        self.assertEqual(issues[0].rule_id, "no-floating-promises")
        self.assertEqual(issues[1].severity, "medium")
        self.assertEqual(issues[0].language, "typescript")

    def test_empty_or_garbage_input_yields_no_issues(self) -> None:
        self.assertEqual(debt_scan.parse_eslint("", FAKE_ROOT), [])
        self.assertEqual(debt_scan.parse_eslint("not json", FAKE_ROOT), [])
        self.assertEqual(debt_scan.parse_eslint("{}", FAKE_ROOT), [])


class TestParseTsc(unittest.TestCase):
    def test_paren_format(self) -> None:
        text = "src/x.ts(10,5): error TS2322: Type 'x' is not assignable to type 'y'."
        issues = debt_scan.parse_tsc(text, "", FAKE_ROOT)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].rule_id, "TS2322")
        self.assertEqual(issues[0].line, 10)
        self.assertEqual(issues[0].column, 5)
        self.assertEqual(issues[0].severity, "high")

    def test_colon_format(self) -> None:
        text = "src/x.ts:42:1 - error TS1234: Something else"
        issues = debt_scan.parse_tsc("", text, FAKE_ROOT)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].rule_id, "TS1234")
        self.assertEqual(issues[0].line, 42)


class TestParseRuff(unittest.TestCase):
    def test_parses_codes_and_locations(self) -> None:
        payload = json.dumps([
            {
                "code": "F401",
                "message": "unused import",
                "filename": str(FAKE_ROOT / "pkg/a.py"),
                "location": {"row": 3, "column": 1},
                "url": "https://example/F401",
            },
            {
                "code": "S101",
                "message": "use of assert",
                "filename": str(FAKE_ROOT / "tests/x.py"),
                "location": {"row": 5, "column": 1},
            },
        ])
        issues = debt_scan.parse_ruff(payload, FAKE_ROOT)
        self.assertEqual(len(issues), 2)
        # F-rules map to high; S-rules map to medium (security family).
        self.assertEqual(issues[0].severity, "high")
        self.assertEqual(issues[1].severity, "medium")
        self.assertEqual(issues[0].help_uri, "https://example/F401")


class TestParseMypy(unittest.TestCase):
    def test_parses_errors_skips_notes(self) -> None:
        text = "\n".join([
            "src/a.py:10:5: error: Incompatible types  [assignment]",
            "src/a.py:11: note: ignored note line",
            "src/b.py:20: error: Missing return  [return]",
        ])
        issues = debt_scan.parse_mypy(text, "", FAKE_ROOT)
        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0].rule_id, "assignment")
        self.assertEqual(issues[0].column, 5)
        self.assertIsNone(issues[1].column)
        self.assertEqual(issues[1].rule_id, "return")


class TestParseBandit(unittest.TestCase):
    def test_parses_results(self) -> None:
        payload = json.dumps({
            "results": [
                {
                    "test_id": "B101",
                    "issue_severity": "HIGH",
                    "issue_confidence": "HIGH",
                    "issue_text": "Use of assert detected",
                    "filename": str(FAKE_ROOT / "x.py"),
                    "line_number": 7,
                    "more_info": "https://example/B101",
                }
            ]
        })
        issues = debt_scan.parse_bandit(payload, FAKE_ROOT)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].rule_id, "B101")
        self.assertEqual(issues[0].severity, "high")
        self.assertEqual(issues[0].confidence, "HIGH")
        self.assertEqual(issues[0].help_uri, "https://example/B101")


class TestParseSemgrep(unittest.TestCase):
    def test_basic(self) -> None:
        payload = json.dumps({
            "results": [
                {
                    "check_id": "rules.foo",
                    "path": str(FAKE_ROOT / "src/a.py"),
                    "start": {"line": 3, "col": 1},
                    "extra": {"severity": "ERROR", "message": "boom",
                              "metadata": {"category": "security", "source": "https://src", "references": ["https://r1", "https://r2"]}},
                }
            ]
        })
        issues = debt_scan.parse_semgrep(payload, FAKE_ROOT)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "high")
        self.assertEqual(issues[0].help_uri, "https://src")
        self.assertEqual(issues[0].category, "security")

    def test_help_uri_falls_back_to_first_reference(self) -> None:
        payload = json.dumps({
            "results": [
                {
                    "check_id": "rules.bar",
                    "path": str(FAKE_ROOT / "src/a.py"),
                    "start": {"line": 1, "col": 1},
                    "extra": {"severity": "WARNING", "message": "m",
                              "metadata": {"references": ["https://only-ref"]}},
                }
            ]
        })
        issues = debt_scan.parse_semgrep(payload, FAKE_ROOT)
        self.assertEqual(issues[0].help_uri, "https://only-ref")

    def test_help_uri_empty_references_does_not_crash(self) -> None:
        """Regression: empty `references` list + no `source` used to raise IndexError."""
        payload = json.dumps({
            "results": [
                {
                    "check_id": "rules.baz",
                    "path": str(FAKE_ROOT / "src/a.py"),
                    "start": {"line": 1, "col": 1},
                    "extra": {"severity": "INFO", "message": "m",
                              "metadata": {"references": []}},
                }
            ]
        })
        issues = debt_scan.parse_semgrep(payload, FAKE_ROOT)
        self.assertEqual(len(issues), 1)
        self.assertIsNone(issues[0].help_uri)

    def test_help_uri_non_list_references_with_source(self) -> None:
        """Regression: when references is a scalar (not list), source should still win."""
        payload = json.dumps({
            "results": [
                {
                    "check_id": "rules.qux",
                    "path": str(FAKE_ROOT / "src/a.py"),
                    "start": {"line": 1, "col": 1},
                    "extra": {"severity": "WARNING", "message": "m",
                              "metadata": {"source": "https://kept", "references": "not-a-list"}},
                }
            ]
        })
        issues = debt_scan.parse_semgrep(payload, FAKE_ROOT)
        self.assertEqual(issues[0].help_uri, "https://kept")


class TestParseCppcheck(unittest.TestCase):
    def test_parses_xml(self) -> None:
        xml = """<?xml version="1.0"?>
<results version="2">
  <errors>
    <error id="nullPointer" severity="error" msg="Null deref" verbose="Null deref verbose">
      <location file="src/a.cpp" line="42" column="7"/>
    </error>
    <error id="unusedVariable" severity="style" msg="Unused var">
      <location file="src/b.cpp" line="5"/>
    </error>
  </errors>
</results>"""
        issues = debt_scan.parse_cppcheck(xml, FAKE_ROOT)
        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0].rule_id, "nullPointer")
        self.assertEqual(issues[0].severity, "high")
        self.assertEqual(issues[0].line, 42)
        self.assertEqual(issues[1].severity, "low")

    def test_blank_input_returns_empty(self) -> None:
        self.assertEqual(debt_scan.parse_cppcheck("", FAKE_ROOT), [])
        self.assertEqual(debt_scan.parse_cppcheck("not xml", FAKE_ROOT), [])


class TestParseClangTidy(unittest.TestCase):
    def test_parses_warnings(self) -> None:
        text = "\n".join([
            "src/a.cpp:12:3: warning: do not use std::endl [modernize-use-default]",
            "src/b.cpp:1:1: error: bad thing [bugprone-foo]",
        ])
        issues = debt_scan.parse_clang_tidy(text, FAKE_ROOT)
        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0].rule_id, "modernize-use-default")
        self.assertEqual(issues[0].severity, "medium")
        self.assertEqual(issues[1].severity, "high")


class TestParseClippy(unittest.TestCase):
    def test_parses_compiler_messages(self) -> None:
        lines = [
            json.dumps({"reason": "build-script-executed"}),  # ignored
            json.dumps({
                "reason": "compiler-message",
                "message": {
                    "level": "warning",
                    "message": "useless clone",
                    "code": {"code": "clippy::redundant_clone"},
                    "spans": [{"is_primary": True, "file_name": "src/main.rs", "line_start": 7, "column_start": 4}],
                },
            }),
            json.dumps({
                "reason": "compiler-message",
                "message": {
                    "level": "warning",
                    "message": "ignored because rule == 'clippy'",
                    "code": {"code": "clippy"},
                    "spans": [{"is_primary": True, "file_name": "src/x.rs", "line_start": 1, "column_start": 1}],
                },
            }),
        ]
        issues = debt_scan.parse_clippy("\n".join(lines), FAKE_ROOT)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].rule_id, "clippy::redundant_clone")
        self.assertEqual(issues[0].severity, "medium")
        self.assertEqual(issues[0].language, "rust")


class TestParseCargoAudit(unittest.TestCase):
    def test_dict_list_form(self) -> None:
        payload = json.dumps({
            "vulnerabilities": {
                "list": [
                    {
                        "advisory": {
                            "id": "RUSTSEC-2024-0001",
                            "title": "CVE in foo",
                            "severity": "critical",
                            "url": "https://rustsec/0001",
                        },
                        "package": {"name": "foo"},
                    }
                ]
            }
        })
        issues = debt_scan.parse_cargo_audit(payload, "", FAKE_ROOT)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].rule_id, "RUSTSEC-2024-0001")
        self.assertEqual(issues[0].severity, "critical")
        self.assertEqual(issues[0].help_uri, "https://rustsec/0001")

    def test_text_fallback(self) -> None:
        text = "\n".join([
            "  ID: RUSTSEC-2023-9999",
            "  Title: Some scary thing",
        ])
        issues = debt_scan.parse_cargo_audit("", text, FAKE_ROOT)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].rule_id, "RUSTSEC-2023-9999")
        self.assertEqual(issues[0].severity, "high")


if __name__ == "__main__":
    unittest.main()
