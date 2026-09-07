#!/usr/bin/env python3
"""
Tech Debt Harness scanner.

Runs configured static-analysis tools, normalizes their output, estimates debt
principal and monthly interest, and optionally compares against a baseline.

No third-party Python dependencies are required.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT_MARKERS = [".debt-harness.json", ".git", "pyproject.toml", "package.json", "Cargo.toml"]
DEFAULT_EXCLUDE_DIRS = {
    ".git", ".debt", ".venv", "venv", "env", "node_modules", "dist", "build", "target",
    ".mypy_cache", ".ruff_cache", "__pycache__", "coverage", ".next",
}
C_CPP_EXTS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
TS_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mts", ".cts"}
PY_EXTS = {".py", ".pyi"}
RUST_EXTS = {".rs"}


@dataclasses.dataclass
class ToolRun:
    tool: str
    command: list[str]
    returncode: int | None
    duration_seconds: float
    stdout_path: str | None = None
    stderr_path: str | None = None
    skipped: bool = False
    skip_reason: str | None = None
    crashed: bool = False
    crash_reason: str | None = None


@dataclasses.dataclass
class Issue:
    tool: str
    language: str
    rule_id: str
    severity: str
    message: str
    file: str
    line: int | None = None
    column: int | None = None
    raw_severity: str | None = None
    category: str | None = None
    confidence: str | None = None
    help_uri: str | None = None
    principal_minutes: int = 0
    monthly_interest_minutes: int = 0
    churn_90d: int = 0
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def now_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for parent in [cur, *cur.parents]:
        if any((parent / marker).exists() for marker in ROOT_MARKERS):
            return parent
    return cur


def load_config(root: Path) -> dict[str, Any]:
    cfg_path = root / ".debt-harness.json"
    if not cfg_path.exists():
        cfg_path = root / "debt-harness.json"
    if cfg_path.exists():
        try:
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid config {cfg_path}: {exc}")
    return {
        "version": 1,
        "output_dir": ".debt",
        "exclude_dirs": sorted(DEFAULT_EXCLUDE_DIRS),
        "tools": {},
        "severity_principal_minutes": {"critical": 120, "high": 60, "medium": 30, "low": 12, "info": 5},
        "severity_monthly_interest_rate": {"critical": 0.35, "high": 0.25, "medium": 0.12, "low": 0.05, "info": 0.02},
        "gates": {
            "max_new_critical": 0,
            "max_new_high": 0,
            "max_monthly_interest_delta_minutes": 60,
            "max_total_issue_delta": 25,
            "fail_on_tool_crash": False,
        },
    }


def relpath(path: str | Path, root: Path) -> str:
    if not path:
        return ""
    p = Path(path)
    try:
        if not p.is_absolute():
            p = (root / p).resolve()
        return p.relative_to(root.resolve()).as_posix()
    except Exception:
        return str(path).replace("\\", "/")


def is_harness_file(path: Path, root: Path) -> bool:
    rel = relpath(path, root)
    return rel in {
        "scripts/debt_scan.py",
    }


def path_is_excluded(file: str, exclude_dirs: Iterable[str]) -> bool:
    """Return True if any segment of `file` (posix-style relpath) is in `exclude_dirs`.

    Tools like bandit only honour `-x ./.venv` for a top-level `.venv`; a nested
    `python/.venv/` slips through. Centralising the check here lets us drop
    vendored noise regardless of which tool reported it.
    """
    if not file:
        return False
    excludes = set(exclude_dirs) | DEFAULT_EXCLUDE_DIRS
    return any(seg in excludes for seg in file.split("/"))


def iter_source_files(root: Path, exclude_dirs: Iterable[str], include_harness: bool = False) -> Iterable[Path]:
    excludes = set(exclude_dirs) | DEFAULT_EXCLUDE_DIRS
    for dirpath, dirnames, filenames in os.walk(root):
        dirpath_p = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in excludes]
        for name in filenames:
            p = dirpath_p / name
            if not include_harness and is_harness_file(p, root):
                continue
            if p.suffix.lower() in C_CPP_EXTS | TS_EXTS | PY_EXTS | RUST_EXTS:
                yield p


def detect_languages(root: Path, cfg: dict[str, Any]) -> dict[str, bool]:
    ex = cfg.get("exclude_dirs", [])
    suffixes = Counter(p.suffix.lower() for p in iter_source_files(root, ex))
    return {
        "typescript": bool((root / "package.json").exists() or (root / "tsconfig.json").exists() or any(suffixes[s] for s in TS_EXTS)),
        "python": bool((root / "pyproject.toml").exists() or (root / "requirements.txt").exists() or any(suffixes[s] for s in PY_EXTS)),
        "c_cpp": bool((root / "compile_commands.json").exists() or (root / "CMakeLists.txt").exists() or any(suffixes[s] for s in C_CPP_EXTS)),
        "rust": bool((root / "Cargo.toml").exists() or any(suffixes[s] for s in RUST_EXTS)),
    }


def which(program: str) -> bool:
    return shutil.which(program) is not None


def normalize_severity(tool: str, raw: Any) -> str:
    r = str(raw or "").strip().lower()
    if tool == "eslint":
        return "high" if r == "2" or r == "error" else "medium" if r == "1" or r == "warn" else "info"
    if tool == "ruff":
        # Ruff does not assign severity. Treat most findings as low/medium depending on rule family.
        if r.startswith(("s", "bandit")):
            return "medium"
        if r.startswith(("f", "e9")):
            return "high"
        return "low"
    if tool == "mypy" or tool == "tsc":
        return "high" if r == "error" else "medium"
    if tool == "bandit":
        if r == "high":
            return "high"
        if r == "medium":
            return "medium"
        return "low"
    if tool == "semgrep":
        if r in {"error", "critical", "high"}:
            return "high"
        if r in {"warning", "medium"}:
            return "medium"
        return "low"
    if tool == "cppcheck":
        if r in {"error"}:
            return "high"
        if r in {"warning", "performance", "portability"}:
            return "medium"
        if r in {"style"}:
            return "low"
        return "info"
    if tool == "clang_tidy":
        if r == "error":
            return "high"
        if r == "warning":
            return "medium"
        return "low"
    if tool == "clippy":
        if r == "error":
            return "high"
        if r == "warning":
            return "medium"
        return "low"
    if tool == "cargo_audit":
        if r in {"critical", "high"}:
            return "critical" if r == "critical" else "high"
        return "high"
    return "medium"


def stable_fingerprint(issue: Issue) -> str:
    payload = "|".join([
        issue.tool,
        issue.rule_id or "",
        issue.file or "",
        str(issue.line or 0),
        re.sub(r"\s+", " ", issue.message or "")[:220],
    ])
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()[:20]


def estimate_debt(issue: Issue, cfg: dict[str, Any], churn_by_file: dict[str, int]) -> Issue:
    sev_minutes = cfg.get("severity_principal_minutes", {})
    sev_rates = cfg.get("severity_monthly_interest_rate", {})
    tool_cfg = cfg.get("tools", {}).get(issue.tool, {})
    multiplier = float(tool_cfg.get("principal_multiplier", 1.0))
    base = int(sev_minutes.get(issue.severity, 30))

    # Security/tool-specific escalation.
    if issue.tool in {"bandit", "semgrep", "cargo_audit"} and issue.severity in {"critical", "high"}:
        multiplier *= 1.2

    issue.principal_minutes = max(1, round(base * multiplier))
    issue.churn_90d = churn_by_file.get(issue.file, 0)
    exposure = 1.0 + min(issue.churn_90d / 8.0, 2.0)
    monthly_rate = float(sev_rates.get(issue.severity, 0.10))
    issue.monthly_interest_minutes = max(0, round(issue.principal_minutes * monthly_rate * exposure))
    issue.fingerprint = stable_fingerprint(issue)
    return issue


def resolve_executable(name: str) -> str:
    """Resolve a bare command name to an absolute path that CreateProcess accepts.

    On Windows, `subprocess.run(["npx", ...])` fails with FileNotFoundError even
    when `npx.cmd` is on PATH, because CreateProcessW does not honour PATHEXT and
    will not auto-discover wrapper scripts. `shutil.which` does honour PATHEXT,
    so resolving first yields a path like `C:\\...\\npx.cmd` that CreateProcessW
    will then route through cmd.exe. POSIX shells already do this implicitly.
    """
    if os.sep in name or name.endswith((".exe", ".cmd", ".bat")):
        return name
    resolved = shutil.which(name)
    return resolved or name


def run_command(
    tool: str,
    cmd: list[str],
    root: Path,
    raw_dir: Path,
    timeout: int = 900,
    cwd: Path | None = None,
) -> tuple[ToolRun, str, str]:
    start = dt.datetime.now(dt.timezone.utc)
    safe_tool = tool.replace("/", "_")
    stdout_path = raw_dir / f"{safe_tool}.stdout"
    stderr_path = raw_dir / f"{safe_tool}.stderr"

    if cmd:
        cmd = [resolve_executable(cmd[0]), *cmd[1:]]

    # Windows defaults subprocess text decoding to the active ANSI code page
    # such as GBK on zh-CN systems. Many tools used here emit UTF-8 JSON/text
    # or Unicode punctuation, so decode as UTF-8 and replace malformed bytes.
    # This prevents background reader-thread UnicodeDecodeError crashes while
    # preserving enough output for parsers and raw logs.
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("NO_COLOR", "1")

    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd or root,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=env,
        )
        stdout_path.write_text(proc.stdout or "", encoding="utf-8", errors="replace")
        stderr_path.write_text(proc.stderr or "", encoding="utf-8", errors="replace")
        dur = (dt.datetime.now(dt.timezone.utc) - start).total_seconds()
        return ToolRun(tool, cmd, proc.returncode, dur, str(stdout_path), str(stderr_path)), proc.stdout or "", proc.stderr or ""
    except FileNotFoundError as exc:
        dur = (dt.datetime.now(dt.timezone.utc) - start).total_seconds()
        tr = ToolRun(tool, cmd, None, dur, skipped=True, skip_reason=f"command not found: {cmd[0]}")
        return tr, "", str(exc)
    except subprocess.TimeoutExpired as exc:
        dur = (dt.datetime.now(dt.timezone.utc) - start).total_seconds()
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        stdout_path.write_text(stdout, encoding="utf-8", errors="replace")
        stderr_path.write_text(stderr, encoding="utf-8", errors="replace")
        tr = ToolRun(tool, cmd, None, dur, str(stdout_path), str(stderr_path), crashed=True, crash_reason="timeout")
        return tr, stdout, stderr


def parse_json_maybe(text: str) -> Any | None:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        # Some tools write log lines before JSON. Try the first JSON-looking span.
        start = min([i for i in [text.find("{"), text.find("[")] if i >= 0], default=-1)
        if start >= 0:
            try:
                return json.loads(text[start:])
            except Exception:
                return None
    return None


def parse_eslint(stdout: str, root: Path) -> list[Issue]:
    data = parse_json_maybe(stdout)
    issues: list[Issue] = []
    if not isinstance(data, list):
        return issues
    for file_result in data:
        file_path = relpath(file_result.get("filePath", ""), root)
        for m in file_result.get("messages", []) or []:
            rule = m.get("ruleId") or "eslint"
            raw_sev = str(m.get("severity", ""))
            issues.append(Issue(
                tool="eslint",
                language="typescript",
                rule_id=rule,
                severity=normalize_severity("eslint", raw_sev),
                raw_severity=raw_sev,
                message=m.get("message", ""),
                file=file_path,
                line=m.get("line"),
                column=m.get("column"),
                category="lint",
            ))
    return issues


def parse_tsc(stdout: str, stderr: str, root: Path) -> list[Issue]:
    text = "\n".join([stdout, stderr])
    issues: list[Issue] = []
    # Examples:
    # src/x.ts(10,5): error TS2322: Type 'x' is not assignable...
    # src/x.ts:10:5 - error TS2322: Type ...
    patterns = [
        re.compile(r"^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)\):\s+(?P<sev>error|warning)\s+(?P<rule>TS\d+):\s+(?P<msg>.+)$"),
        re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+)\s+-\s+(?P<sev>error|warning)\s+(?P<rule>TS\d+):\s+(?P<msg>.+)$"),
    ]
    for line in text.splitlines():
        for pat in patterns:
            m = pat.match(line.strip())
            if m:
                issues.append(Issue(
                    tool="tsc",
                    language="typescript",
                    rule_id=m.group("rule"),
                    severity=normalize_severity("tsc", m.group("sev")),
                    raw_severity=m.group("sev"),
                    message=m.group("msg"),
                    file=relpath(m.group("file"), root),
                    line=int(m.group("line")),
                    column=int(m.group("col")),
                    category="type",
                ))
                break
    return issues


def parse_ruff(stdout: str, root: Path) -> list[Issue]:
    data = parse_json_maybe(stdout)
    issues: list[Issue] = []
    if not isinstance(data, list):
        return issues
    for item in data:
        code = item.get("code") or "ruff"
        loc = item.get("location") or {}
        issues.append(Issue(
            tool="ruff",
            language="python",
            rule_id=code,
            severity=normalize_severity("ruff", code),
            raw_severity=code,
            message=item.get("message", ""),
            file=relpath(item.get("filename", ""), root),
            line=loc.get("row"),
            column=loc.get("column"),
            category="lint",
            help_uri=item.get("url"),
        ))
    return issues


def parse_mypy(stdout: str, stderr: str, root: Path) -> list[Issue]:
    text = "\n".join([stdout, stderr])
    issues: list[Issue] = []
    pat = re.compile(r"^(?P<file>.+?):(?P<line>\d+)(?::(?P<col>\d+))?:\s+(?P<sev>error|note|warning):\s+(?P<msg>.*?)(?:\s+\[(?P<rule>[^\]]+)\])?$")
    for line in text.splitlines():
        m = pat.match(line.strip())
        if not m:
            continue
        sev = m.group("sev")
        if sev == "note":
            continue
        issues.append(Issue(
            tool="mypy",
            language="python",
            rule_id=m.group("rule") or "mypy",
            severity=normalize_severity("mypy", sev),
            raw_severity=sev,
            message=m.group("msg"),
            file=relpath(m.group("file"), root),
            line=int(m.group("line")),
            column=int(m.group("col")) if m.group("col") else None,
            category="type",
        ))
    return issues


def parse_bandit(stdout: str, root: Path) -> list[Issue]:
    data = parse_json_maybe(stdout)
    issues: list[Issue] = []
    if not isinstance(data, dict):
        return issues
    for item in data.get("results", []) or []:
        raw_sev = item.get("issue_severity", "")
        issues.append(Issue(
            tool="bandit",
            language="python",
            rule_id=item.get("test_id") or item.get("test_name") or "bandit",
            severity=normalize_severity("bandit", raw_sev),
            raw_severity=raw_sev,
            confidence=item.get("issue_confidence"),
            message=item.get("issue_text", ""),
            file=relpath(item.get("filename", ""), root),
            line=item.get("line_number"),
            column=None,
            category="security",
            help_uri=item.get("more_info"),
        ))
    return issues


def parse_semgrep(stdout: str, root: Path) -> list[Issue]:
    data = parse_json_maybe(stdout)
    issues: list[Issue] = []
    if not isinstance(data, dict):
        return issues
    for item in data.get("results", []) or []:
        extra = item.get("extra") or {}
        metadata = extra.get("metadata") or {}
        start = item.get("start") or {}
        path = item.get("path", "")
        raw_sev = extra.get("severity") or metadata.get("severity") or "warning"
        lang = "typescript"
        suffix = Path(path).suffix.lower()
        if suffix in PY_EXTS:
            lang = "python"
        elif suffix in C_CPP_EXTS:
            lang = "c_cpp"
        elif suffix in RUST_EXTS:
            lang = "rust"
        refs = metadata.get("references")
        first_ref = refs[0] if isinstance(refs, list) and refs else None
        help_uri = metadata.get("source") or first_ref
        issues.append(Issue(
            tool="semgrep",
            language=lang,
            rule_id=item.get("check_id") or "semgrep",
            severity=normalize_severity("semgrep", raw_sev),
            raw_severity=raw_sev,
            message=extra.get("message") or item.get("extra", {}).get("lines", ""),
            file=relpath(path, root),
            line=start.get("line"),
            column=start.get("col"),
            category="security" if "security" in str(metadata).lower() else "policy",
            help_uri=help_uri,
        ))
    return issues


def parse_cppcheck(xml_text: str, root: Path) -> list[Issue]:
    issues: list[Issue] = []
    xml_text = xml_text.strip()
    if not xml_text:
        return issues
    try:
        tree = ET.fromstring(xml_text)
    except ET.ParseError:
        return issues
    for err in tree.findall(".//error"):
        raw_sev = err.attrib.get("severity", "")
        loc = err.find("location")
        file_path = loc.attrib.get("file", "") if loc is not None else err.attrib.get("file0", "")
        line = loc.attrib.get("line") if loc is not None else None
        col = loc.attrib.get("column") if loc is not None else None
        issues.append(Issue(
            tool="cppcheck",
            language="c_cpp",
            rule_id=err.attrib.get("id", "cppcheck"),
            severity=normalize_severity("cppcheck", raw_sev),
            raw_severity=raw_sev,
            message=err.attrib.get("msg") or err.attrib.get("verbose", ""),
            file=relpath(file_path, root),
            line=int(line) if line and line.isdigit() else None,
            column=int(col) if col and col.isdigit() else None,
            category=raw_sev or "analysis",
        ))
    return issues


def parse_clang_tidy(text: str, root: Path) -> list[Issue]:
    issues: list[Issue] = []
    pat = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+):\s+(?P<sev>warning|error):\s+(?P<msg>.*?)(?:\s+\[(?P<rule>[^\]]+)\])?$")
    for line in text.splitlines():
        m = pat.match(line.strip())
        if not m:
            continue
        issues.append(Issue(
            tool="clang_tidy",
            language="c_cpp",
            rule_id=m.group("rule") or "clang-tidy",
            severity=normalize_severity("clang_tidy", m.group("sev")),
            raw_severity=m.group("sev"),
            message=m.group("msg"),
            file=relpath(m.group("file"), root),
            line=int(m.group("line")),
            column=int(m.group("col")),
            category="lint",
        ))
    return issues


def parse_clippy(stdout: str, root: Path) -> list[Issue]:
    issues: list[Issue] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("reason") != "compiler-message":
            continue
        msg = obj.get("message") or {}
        code = msg.get("code") or {}
        spans = msg.get("spans") or []
        primary = next((s for s in spans if s.get("is_primary")), spans[0] if spans else {})
        raw_sev = msg.get("level", "warning")
        rule = code.get("code") or "clippy"
        if rule == "clippy":
            continue
        issues.append(Issue(
            tool="clippy",
            language="rust",
            rule_id=rule,
            severity=normalize_severity("clippy", raw_sev),
            raw_severity=raw_sev,
            message=msg.get("message", ""),
            file=relpath(primary.get("file_name", ""), root),
            line=primary.get("line_start"),
            column=primary.get("column_start"),
            category="lint",
        ))
    return issues


def parse_cargo_audit(stdout: str, stderr: str, root: Path) -> list[Issue]:
    data = parse_json_maybe(stdout) or parse_json_maybe(stderr)
    issues: list[Issue] = []
    if isinstance(data, dict):
        # cargo-audit JSON has changed over time. Support common shapes defensively.
        vulnerabilities = []
        if isinstance(data.get("vulnerabilities"), dict):
            vulnerabilities = data["vulnerabilities"].get("list", []) or []
        elif isinstance(data.get("vulnerabilities"), list):
            vulnerabilities = data.get("vulnerabilities", [])
        elif isinstance(data.get("database"), dict) and isinstance(data.get("warnings"), dict):
            vulnerabilities = data.get("warnings", {}).get("unmaintained", []) or []

        for vuln in vulnerabilities:
            advisory = vuln.get("advisory") if isinstance(vuln.get("advisory"), dict) else vuln
            package = vuln.get("package", {}) if isinstance(vuln.get("package"), dict) else {}
            severity = advisory.get("severity") or advisory.get("cvss", {}).get("severity") or "high"
            rule_id = advisory.get("id") or advisory.get("aliases", ["RUSTSEC"])[0]
            pkg_name = package.get("name") or vuln.get("package", "") or "dependency"
            msg = advisory.get("title") or advisory.get("description") or f"Vulnerable Rust dependency: {pkg_name}"
            issues.append(Issue(
                tool="cargo_audit",
                language="rust",
                rule_id=str(rule_id),
                severity=normalize_severity("cargo_audit", severity),
                raw_severity=str(severity),
                message=str(msg).splitlines()[0],
                file="Cargo.lock" if (root / "Cargo.lock").exists() else "Cargo.toml",
                line=None,
                column=None,
                category="dependency-security",
                help_uri=advisory.get("url"),
            ))
        return issues

    # Text fallback.
    text = "\n".join([stdout, stderr])
    current_id = None
    current_title = None
    for line in text.splitlines():
        if "ID:" in line and "RUSTSEC" in line:
            current_id = line.split("ID:", 1)[1].strip()
        if "Title:" in line:
            current_title = line.split("Title:", 1)[1].strip()
        if current_id and current_title:
            issues.append(Issue(
                tool="cargo_audit", language="rust", rule_id=current_id, severity="high",
                raw_severity="high", message=current_title, file="Cargo.lock", category="dependency-security"
            ))
            current_id = None
            current_title = None
    return issues


def git_churn_90d(root: Path) -> dict[str, int]:
    if not (root / ".git").exists() or not which("git"):
        return {}
    try:
        proc = subprocess.run(
            ["git", "log", "--since=90.days", "--name-only", "--pretty=format:"],
            cwd=root,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except Exception:
        return {}
    counts: dict[str, int] = defaultdict(int)
    for line in proc.stdout.splitlines():
        line = line.strip().replace("\\", "/")
        if line:
            counts[line] += 1
    return counts


def find_cargo_manifest(root: Path, max_depth: int = 2) -> Path | None:
    """Return the directory containing the first Cargo.toml found within max_depth.

    Tauri and similar layouts keep Cargo.toml under `src-tauri/`, not at the
    repo root. The depth limit prevents walking large vendored trees.
    """
    if (root / "Cargo.toml").exists():
        return root
    excludes = DEFAULT_EXCLUDE_DIRS | {"node_modules", "target", "dist", "build"}
    queue: list[tuple[Path, int]] = [(root, 0)]
    while queue:
        d, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        try:
            for child in d.iterdir():
                if not child.is_dir() or child.name in excludes:
                    continue
                if (child / "Cargo.toml").exists():
                    return child
                queue.append((child, depth + 1))
        except OSError:
            continue
    return None


def find_compile_commands(root: Path) -> Path | None:
    candidates = [root / "compile_commands.json", root / "build" / "compile_commands.json"]
    for c in candidates:
        if c.exists():
            return c
    for p in root.glob("**/compile_commands.json"):
        parts = set(p.parts)
        if ".debt" not in parts and "node_modules" not in parts and "target" not in parts:
            return p
    return None


def tool_enabled(tool_name: str, cfg: dict[str, Any], languages: dict[str, bool], requested: set[str] | None) -> tuple[bool, str | None]:
    if requested is not None and tool_name not in requested:
        return False, "not requested"
    tool_cfg = cfg.get("tools", {}).get(tool_name, {})
    enabled = tool_cfg.get("enabled", "auto")
    if enabled is False or str(enabled).lower() == "false":
        return False, "disabled in config"
    if enabled == "auto" or str(enabled).lower() == "auto":
        langs = tool_cfg.get("languages", [])
        if langs and not any(languages.get(lang, False) for lang in langs):
            return False, "language not detected"
    return True, None


def run_scanners(root: Path, cfg: dict[str, Any], requested_tools: set[str] | None = None) -> tuple[list[Issue], list[ToolRun], Path]:
    out_root = root / cfg.get("output_dir", ".debt")
    run_dir = out_root / "results" / now_stamp()
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    languages = detect_languages(root, cfg)
    issues: list[Issue] = []
    tool_runs: list[ToolRun] = []

    def should(tool: str) -> bool:
        ok, reason = tool_enabled(tool, cfg, languages, requested_tools)
        if not ok:
            tool_runs.append(ToolRun(tool, [], None, 0, skipped=True, skip_reason=reason))
        return ok

    # ESLint
    if should("eslint"):
        cmd = cfg.get("tools", {}).get("eslint", {}).get("command", ["npx", "eslint", ".", "--format", "json"])
        tr, stdout, stderr = run_command("eslint", list(map(str, cmd)), root, raw_dir)
        tool_runs.append(tr)
        issues.extend(parse_eslint(stdout, root))

    # tsc
    if should("tsc"):
        if not (root / "tsconfig.json").exists() and not (root / "tsconfig.debt.json").exists():
            tool_runs.append(ToolRun("tsc", [], None, 0, skipped=True, skip_reason="no tsconfig.json or tsconfig.debt.json"))
        else:
            cmd = cfg.get("tools", {}).get("tsc", {}).get("command", ["npx", "tsc", "-p", "tsconfig.json", "--noEmit", "--pretty", "false"])
            if not (root / "tsconfig.json").exists() and (root / "tsconfig.debt.json").exists():
                cmd = ["npx", "tsc", "-p", "tsconfig.debt.json", "--noEmit", "--pretty", "false"]
            tr, stdout, stderr = run_command("tsc", list(map(str, cmd)), root, raw_dir)
            tool_runs.append(tr)
            issues.extend(parse_tsc(stdout, stderr, root))

    # Ruff
    if should("ruff"):
        cmd = cfg.get("tools", {}).get("ruff", {}).get("command", ["ruff", "check", ".", "--output-format", "json"])
        tr, stdout, stderr = run_command("ruff", list(map(str, cmd)), root, raw_dir)
        tool_runs.append(tr)
        issues.extend(parse_ruff(stdout, root))

    # mypy
    if should("mypy"):
        cmd = cfg.get("tools", {}).get("mypy", {}).get("command", ["mypy", ".", "--no-error-summary", "--show-column-numbers", "--show-error-codes"])
        tr, stdout, stderr = run_command("mypy", list(map(str, cmd)), root, raw_dir)
        tool_runs.append(tr)
        issues.extend(parse_mypy(stdout, stderr, root))

    # Bandit
    if should("bandit"):
        cmd = cfg.get("tools", {}).get("bandit", {}).get("command", ["bandit", "-r", ".", "-f", "json", "-q"])
        tr, stdout, stderr = run_command("bandit", list(map(str, cmd)), root, raw_dir)
        tool_runs.append(tr)
        issues.extend(parse_bandit(stdout, root))

    # Semgrep
    if should("semgrep"):
        if not (root / ".semgrep.yml").exists() and "--config" in cfg.get("tools", {}).get("semgrep", {}).get("command", []):
            tool_runs.append(ToolRun("semgrep", [], None, 0, skipped=True, skip_reason=".semgrep.yml not found"))
        else:
            cmd = cfg.get("tools", {}).get("semgrep", {}).get("command", ["semgrep", "scan", "--config", ".semgrep.yml", "--json", "--quiet"])
            tr, stdout, stderr = run_command("semgrep", list(map(str, cmd)), root, raw_dir)
            tool_runs.append(tr)
            issues.extend(parse_semgrep(stdout, root))

    # cppcheck
    # Command is built dynamically by default because it depends on whether a
    # compile_commands.json and cppcheck-suppressions.txt are present. Users
    # can override by setting `tools.cppcheck.command` in .debt-harness.json.
    if should("cppcheck"):
        cppcheck_cfg = cfg.get("tools", {}).get("cppcheck", {})
        cfg_cmd = cppcheck_cfg.get("command")
        if cfg_cmd:
            cmd = list(map(str, cfg_cmd))
        else:
            compile_db = find_compile_commands(root)
            cmd = ["cppcheck", "--enable=warning,style,performance,portability,information", "--inconclusive", "--xml", "--xml-version=2"]
            if (root / "cppcheck-suppressions.txt").exists():
                cmd.append("--suppressions-list=cppcheck-suppressions.txt")
            if compile_db:
                cmd.append(f"--project={compile_db}")
            else:
                cmd.append(".")
        tr, stdout, stderr = run_command("cppcheck", cmd, root, raw_dir)
        tool_runs.append(tr)
        issues.extend(parse_cppcheck(stderr or stdout, root))

    # clang-tidy
    # `requires_compile_commands: true` in config gates this tool; the command
    # is otherwise built dynamically because it depends on the compile DB path
    # and whether `run-clang-tidy` (the parallel driver) is available. Users
    # can override by setting `tools.clang_tidy.command` in .debt-harness.json.
    if should("clang_tidy"):
        clang_cfg = cfg.get("tools", {}).get("clang_tidy", {})
        compile_db = find_compile_commands(root)
        if clang_cfg.get("requires_compile_commands", True) and not compile_db:
            tool_runs.append(ToolRun("clang_tidy", [], None, 0, skipped=True, skip_reason="compile_commands.json not found"))
        else:
            cfg_cmd = clang_cfg.get("command")
            if cfg_cmd:
                cmd = list(map(str, cfg_cmd))
                tr, stdout, stderr = run_command("clang_tidy", cmd, root, raw_dir, timeout=1800)
                tool_runs.append(tr)
                issues.extend(parse_clang_tidy("\n".join([stdout, stderr]), root))
            elif which("run-clang-tidy"):
                cmd = ["run-clang-tidy", "-p", str(compile_db.parent), "-quiet"]
                tr, stdout, stderr = run_command("clang_tidy", cmd, root, raw_dir, timeout=1800)
                tool_runs.append(tr)
                issues.extend(parse_clang_tidy("\n".join([stdout, stderr]), root))
            else:
                files = [p for p in iter_source_files(root, cfg.get("exclude_dirs", [])) if p.suffix.lower() in C_CPP_EXTS]
                max_files = int(os.environ.get("DEBT_CLANG_TIDY_MAX_FILES", "200"))
                combined = []
                start = dt.datetime.now(dt.timezone.utc)
                for p in files[:max_files]:
                    cmd = ["clang-tidy", str(p), "-p", str(compile_db.parent), "--quiet"]
                    tr, stdout, stderr = run_command("clang_tidy", cmd, root, raw_dir, timeout=300)
                    combined.append(stdout)
                    combined.append(stderr)
                dur = (dt.datetime.now(dt.timezone.utc) - start).total_seconds()
                raw = "\n".join(combined)
                (raw_dir / "clang_tidy.combined").write_text(raw, encoding="utf-8", errors="replace")
                tool_runs.append(ToolRun("clang_tidy", ["clang-tidy", "<files>", "-p", str(compile_db.parent)], 0, dur, str(raw_dir / "clang_tidy.combined")))
                issues.extend(parse_clang_tidy(raw, root))

    # Rust clippy
    # Honour `tools.clippy.cwd` if set; otherwise auto-discover the Cargo
    # manifest (Tauri layouts keep it under src-tauri/, not the repo root).
    if should("clippy"):
        clippy_cfg = cfg.get("tools", {}).get("clippy", {})
        configured_cwd = clippy_cfg.get("cwd")
        clippy_cwd = (root / configured_cwd) if configured_cwd else find_cargo_manifest(root)
        if not clippy_cwd or not (clippy_cwd / "Cargo.toml").exists():
            tool_runs.append(ToolRun("clippy", [], None, 0, skipped=True, skip_reason="Cargo.toml not found"))
        else:
            cmd = clippy_cfg.get("command", ["cargo", "clippy", "--all-targets", "--all-features", "--message-format", "json"])
            tr, stdout, stderr = run_command("clippy", list(map(str, cmd)), root, raw_dir, timeout=1800, cwd=clippy_cwd)
            tool_runs.append(tr)
            issues.extend(parse_clippy(stdout, root))

    # Rust cargo-audit
    if should("cargo_audit"):
        audit_cfg = cfg.get("tools", {}).get("cargo_audit", {})
        configured_cwd = audit_cfg.get("cwd")
        audit_cwd = (root / configured_cwd) if configured_cwd else find_cargo_manifest(root)
        if not audit_cwd or not (audit_cwd / "Cargo.lock").exists():
            tool_runs.append(ToolRun("cargo_audit", [], None, 0, skipped=True, skip_reason="Cargo.lock not found"))
        else:
            cmd = audit_cfg.get("command", ["cargo", "audit", "--json"])
            tr, stdout, stderr = run_command("cargo_audit", list(map(str, cmd)), root, raw_dir, timeout=900, cwd=audit_cwd)
            tool_runs.append(tr)
            issues.extend(parse_cargo_audit(stdout, stderr, root))

    # Drop issues that live inside excluded directories. Tools handle their
    # own `--exclude` flags inconsistently across nested layouts (e.g. bandit
    # treats `-x ./.venv` as a root-level prefix only), so post-filter here
    # to guarantee the harness-level contract.
    exclude_dirs = cfg.get("exclude_dirs", []) or []
    issues = [i for i in issues if not path_is_excluded(i.file, exclude_dirs)]

    churn = git_churn_90d(root)
    issues = [estimate_debt(issue, cfg, churn) for issue in issues]
    issues.sort(key=lambda i: (-(i.monthly_interest_minutes), -(i.principal_minutes), i.tool, i.file, i.line or 0))
    return issues, tool_runs, run_dir


def aggregate(issues: list[Issue]) -> dict[str, Any]:
    by_tool = Counter(i.tool for i in issues)
    by_severity = Counter(i.severity for i in issues)
    by_language = Counter(i.language for i in issues)
    by_file_interest: dict[str, int] = defaultdict(int)
    by_file_count: dict[str, int] = defaultdict(int)
    for i in issues:
        by_file_interest[i.file] += i.monthly_interest_minutes
        by_file_count[i.file] += 1
    return {
        "issue_count": len(issues),
        "principal_minutes": sum(i.principal_minutes for i in issues),
        "monthly_interest_minutes": sum(i.monthly_interest_minutes for i in issues),
        "by_tool": dict(by_tool),
        "by_severity": dict(by_severity),
        "by_language": dict(by_language),
        "top_files_by_interest": [
            {"file": f, "monthly_interest_minutes": m, "issue_count": by_file_count[f]}
            for f, m in sorted(by_file_interest.items(), key=lambda kv: kv[1], reverse=True)[:20]
        ],
    }


def markdown_table(rows: list[list[Any]], headers: list[str]) -> str:
    def esc(x: Any) -> str:
        return str(x).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(esc(x) for x in row) + " |")
    return "\n".join(lines)


def write_outputs(root: Path, cfg: dict[str, Any], run_dir: Path, issues: list[Issue], tool_runs: list[ToolRun]) -> None:
    out_root = root / cfg.get("output_dir", ".debt")
    out_root.mkdir(parents=True, exist_ok=True)
    summary = aggregate(issues)
    payload = {
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "repo_root": str(root),
        "summary": summary,
        "tool_runs": [dataclasses.asdict(t) for t in tool_runs],
        "issues": [i.to_dict() for i in issues],
    }
    (run_dir / "items.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_root / "latest_items.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    tool_rows = [[t.tool, "skipped" if t.skipped else "crashed" if t.crashed else str(t.returncode), f"{t.duration_seconds:.1f}s", t.skip_reason or t.crash_reason or ""] for t in tool_runs]
    sev_rows = [[k, v] for k, v in sorted(summary["by_severity"].items(), key=lambda kv: ["critical", "high", "medium", "low", "info"].index(kv[0]) if kv[0] in ["critical", "high", "medium", "low", "info"] else 99)]
    top_issue_rows = [[
        i.severity,
        i.tool,
        i.rule_id,
        i.file,
        i.line or "",
        i.principal_minutes,
        i.monthly_interest_minutes,
        i.message[:120],
    ] for i in issues[:30]]
    top_file_rows = [[f["file"], f["issue_count"], f["monthly_interest_minutes"]] for f in summary["top_files_by_interest"][:20]]

    md = f"""# Tech Debt Scan Summary

Generated: `{payload['generated_at_utc']}`

## Totals

| Metric | Value |
|---|---:|
| Issues | {summary['issue_count']} |
| Principal | {summary['principal_minutes']} min / {summary['principal_minutes'] / 60:.1f} h |
| Monthly interest | {summary['monthly_interest_minutes']} min / {summary['monthly_interest_minutes'] / 60:.1f} h |

## By severity

{markdown_table(sev_rows, ['Severity', 'Count']) if sev_rows else '_No findings._'}

## Tool runs

{markdown_table(tool_rows, ['Tool', 'Status', 'Duration', 'Note'])}

## Top files by monthly interest

{markdown_table(top_file_rows, ['File', 'Issues', 'Monthly interest min']) if top_file_rows else '_No findings._'}

## Top debt items

{markdown_table(top_issue_rows, ['Severity', 'Tool', 'Rule', 'File', 'Line', 'Principal min', 'Interest min/mo', 'Message']) if top_issue_rows else '_No findings._'}

## Next actions

1. Fix or suppress true positives in the top debt items.
2. For intentional debt, add an entry to `docs/tech-debt-tracker.md`.
3. Run `python3 scripts/debt_scan.py baseline` only after the team accepts the current debt state.
"""
    (run_dir / "summary.md").write_text(md, encoding="utf-8")
    (out_root / "latest_summary.md").write_text(md, encoding="utf-8")


def load_scan_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Missing scan file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def baseline(root: Path, cfg: dict[str, Any]) -> None:
    out_root = root / cfg.get("output_dir", ".debt")
    latest = out_root / "latest_items.json"
    data = load_scan_file(latest)
    baseline_payload = {
        "schema_version": 1,
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "summary": data.get("summary", {}),
        "fingerprints": sorted(i.get("fingerprint") for i in data.get("issues", []) if i.get("fingerprint")),
        "issues": data.get("issues", []),
    }
    (out_root / "baseline.json").write_text(json.dumps(baseline_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Baseline written: {out_root / 'baseline.json'}")


def compare(root: Path, cfg: dict[str, Any], latest_path: Path | None = None) -> tuple[bool, str]:
    out_root = root / cfg.get("output_dir", ".debt")
    latest = load_scan_file(latest_path or (out_root / "latest_items.json"))
    base_path = out_root / "baseline.json"
    if not base_path.exists():
        return True, "No baseline found; skipping comparison. Run `python3 scripts/debt_scan.py baseline` to create one."
    base = load_scan_file(base_path)
    gates = cfg.get("gates", {})

    base_fps = set(base.get("fingerprints", [])) or {i.get("fingerprint") for i in base.get("issues", []) if i.get("fingerprint")}
    latest_issues = latest.get("issues", [])
    new_issues = [i for i in latest_issues if i.get("fingerprint") not in base_fps]
    new_sev = Counter(i.get("severity") for i in new_issues)
    latest_interest = int(latest.get("summary", {}).get("monthly_interest_minutes", 0))
    base_interest = int(base.get("summary", {}).get("monthly_interest_minutes", 0))
    interest_delta = latest_interest - base_interest
    issue_delta = int(latest.get("summary", {}).get("issue_count", 0)) - int(base.get("summary", {}).get("issue_count", 0))

    failures = []
    if new_sev.get("critical", 0) > int(gates.get("max_new_critical", 0)):
        failures.append(f"new critical issues: {new_sev.get('critical', 0)} > {gates.get('max_new_critical', 0)}")
    if new_sev.get("high", 0) > int(gates.get("max_new_high", 0)):
        failures.append(f"new high issues: {new_sev.get('high', 0)} > {gates.get('max_new_high', 0)}")
    if interest_delta > int(gates.get("max_monthly_interest_delta_minutes", 60)):
        failures.append(f"monthly interest delta: {interest_delta} min > {gates.get('max_monthly_interest_delta_minutes', 60)} min")
    if issue_delta > int(gates.get("max_total_issue_delta", 25)):
        failures.append(f"issue count delta: {issue_delta} > {gates.get('max_total_issue_delta', 25)}")

    message = textwrap.dedent(f"""
    Baseline comparison:
      latest issues: {latest.get('summary', {}).get('issue_count', 0)}
      baseline issues: {base.get('summary', {}).get('issue_count', 0)}
      issue delta: {issue_delta}
      monthly interest delta: {interest_delta} min
      new issues: {len(new_issues)} ({dict(new_sev)})
    """).strip()
    if failures:
        message += "\nGate failures:\n" + "\n".join(f"  - {f}" for f in failures)
        return False, message
    return True, message


def doctor(root: Path, cfg: dict[str, Any]) -> int:
    languages = detect_languages(root, cfg)
    tools = ["eslint", "npx", "tsc", "ruff", "mypy", "bandit", "semgrep", "cppcheck", "clang-tidy", "run-clang-tidy", "cargo", "cargo-audit", "git"]
    print(f"Repo: {root}")
    print("Detected languages:")
    for lang, present in languages.items():
        print(f"  {lang:12} {'yes' if present else 'no'}")
    print("Tools:")
    for t in tools:
        print(f"  {t:16} {'found' if which(t) else 'missing'}")
    cc = find_compile_commands(root)
    print(f"compile_commands.json: {cc if cc else 'not found'}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    root = find_repo_root(Path(args.repo or os.getcwd()))
    cfg = load_config(root)
    requested = set(args.tools.split(",")) if args.tools else None
    issues, tool_runs, run_dir = run_scanners(root, cfg, requested)
    write_outputs(root, cfg, run_dir, issues, tool_runs)
    summary = aggregate(issues)
    print(f"Scan complete: {run_dir}")
    print(f"Issues: {summary['issue_count']} | Principal: {summary['principal_minutes'] / 60:.1f}h | Monthly interest: {summary['monthly_interest_minutes'] / 60:.1f}h")
    print(f"Summary: {root / cfg.get('output_dir', '.debt') / 'latest_summary.md'}")

    exit_ok = True
    messages = []
    if cfg.get("gates", {}).get("fail_on_tool_crash", False):
        crashed = [t for t in tool_runs if t.crashed]
        if crashed:
            exit_ok = False
            messages.append("Tool crashes: " + ", ".join(t.tool for t in crashed))
    if args.ci:
        ok, msg = compare(root, cfg)
        print(msg)
        exit_ok = exit_ok and ok
    if messages:
        print("\n".join(messages), file=sys.stderr)
    return 0 if exit_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run static analyzers and quantify technical debt.")
    parser.add_argument("--repo", help="Repository root. Defaults to current directory or nearest repo marker.")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_p = sub.add_parser("scan", help="Run analyzers and write .debt/latest_* outputs.")
    scan_p.add_argument("--tools", help="Comma-separated subset, e.g. ruff,mypy,eslint,clippy")
    scan_p.add_argument("--ci", action="store_true", help="Compare to baseline and apply gates.")

    sub.add_parser("baseline", help="Accept current .debt/latest_items.json as baseline.")
    sub.add_parser("compare", help="Compare .debt/latest_items.json to .debt/baseline.json.")
    sub.add_parser("doctor", help="Print detected languages and installed tools.")

    args = parser.parse_args(argv)
    root = find_repo_root(Path(args.repo or os.getcwd()))
    cfg = load_config(root)
    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "baseline":
        baseline(root, cfg)
        return 0
    if args.command == "compare":
        ok, msg = compare(root, cfg)
        print(msg)
        return 0 if ok else 1
    if args.command == "doctor":
        return doctor(root, cfg)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
