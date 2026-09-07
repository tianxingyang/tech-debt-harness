"""Tests for the cross-cutting helpers: path filtering, cargo manifest lookup,
and the Windows-friendly executable resolver."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _harness import debt_scan


class TestPathIsExcluded(unittest.TestCase):
    def test_nested_venv_is_excluded(self) -> None:
        # The whole reason this helper exists: bandit reports findings under
        # `python/.venv/...` which the tool's own --exclude does not filter.
        self.assertTrue(debt_scan.path_is_excluded("python/.venv/Lib/site-packages/numpy/x.py", []))

    def test_top_level_excluded_dirs(self) -> None:
        for p in [
            "node_modules/foo/x.js",
            ".venv/bin/x",
            "dist/bundle.js",
            "build/out.o",
            "target/debug/x",
            ".debt/results/latest_items.json",
            "__pycache__/x.pyc",
        ]:
            self.assertTrue(debt_scan.path_is_excluded(p, []), p)

    def test_real_code_is_kept(self) -> None:
        for p in ["src/a.ts", "python/main.py", "src-tauri/src/lib.rs", "scripts/x.py"]:
            self.assertFalse(debt_scan.path_is_excluded(p, []), p)

    def test_extra_exclude_dirs_honored(self) -> None:
        self.assertTrue(debt_scan.path_is_excluded("vendor/foo.go", ["vendor"]))

    def test_empty_path_is_kept(self) -> None:
        # cargo_audit emits Cargo.lock-relative findings; an empty path should
        # never be silently dropped.
        self.assertFalse(debt_scan.path_is_excluded("", []))


class TestFindCargoManifest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_returns_root_when_root_has_manifest(self) -> None:
        (self.root / "Cargo.toml").write_text("", encoding="utf-8")
        self.assertEqual(debt_scan.find_cargo_manifest(self.root), self.root)

    def test_finds_tauri_style_subproject(self) -> None:
        sub = self.root / "src-tauri"
        sub.mkdir()
        (sub / "Cargo.toml").write_text("", encoding="utf-8")
        self.assertEqual(debt_scan.find_cargo_manifest(self.root), sub)

    def test_returns_none_when_absent(self) -> None:
        self.assertIsNone(debt_scan.find_cargo_manifest(self.root))

    def test_skips_excluded_dirs(self) -> None:
        # A Cargo.toml inside node_modules should not be picked up.
        nm = self.root / "node_modules" / "fake-crate"
        nm.mkdir(parents=True)
        (nm / "Cargo.toml").write_text("", encoding="utf-8")
        self.assertIsNone(debt_scan.find_cargo_manifest(self.root))

    def test_respects_depth_limit(self) -> None:
        deep = self.root / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "Cargo.toml").write_text("", encoding="utf-8")
        # default max_depth=2 should not reach a/b/c
        self.assertIsNone(debt_scan.find_cargo_manifest(self.root))


class TestResolveExecutable(unittest.TestCase):
    def test_absolute_path_is_unchanged(self) -> None:
        absolute = os.path.join("C:" + os.sep if os.name == "nt" else "/", "tools", "ruff")
        self.assertEqual(debt_scan.resolve_executable(absolute), absolute)

    def test_known_extension_unchanged(self) -> None:
        for name in ["npx.cmd", "tool.exe", "x.bat"]:
            self.assertEqual(debt_scan.resolve_executable(name), name)

    def test_resolves_via_shutil_which(self) -> None:
        with patch.object(shutil, "which", return_value="/resolved/npx.cmd"):
            self.assertEqual(debt_scan.resolve_executable("npx"), "/resolved/npx.cmd")

    def test_falls_back_to_name_when_not_found(self) -> None:
        with patch.object(shutil, "which", return_value=None):
            # Falling back to the bare name lets subprocess raise FileNotFoundError
            # which run_command then turns into a structured skip.
            self.assertEqual(debt_scan.resolve_executable("definitely-not-installed-xyz"),
                             "definitely-not-installed-xyz")


if __name__ == "__main__":
    unittest.main()
