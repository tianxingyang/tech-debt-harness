# Tech Debt Harness

A repo-local static-analysis and technical-debt quantification harness for TypeScript, C++, C, Python, and Rust.

The goal is not just to run linters. The goal is to turn static-analysis output into a repeatable debt ledger:

```text
raw analyzer findings -> normalized debt items -> principal estimate -> monthly interest estimate -> baseline/gate
```

This scaffold is intentionally plain: Bash or PowerShell + Python standard library + common language-native tools. Python CLI analyzers are installed and managed with `uv tool`, not pipx/pip. It can be dropped into an existing repository without introducing a large platform.

## Supported languages and tools

| Language | Primary tools | What they catch |
|---|---|---|
| TypeScript | ESLint + typescript-eslint, tsc | unsafe patterns, type errors, maintainability issues |
| Python | Ruff, mypy, Bandit, optional Semgrep | lint, typing, security, insecure patterns |
| C / C++ | clang-tidy, cppcheck | bug-prone constructs, undefined behavior, portability, style |
| Rust | cargo clippy, cargo audit | lints, correctness, dependency vulnerabilities |
| Cross-language | Semgrep | security and custom policy rules |

The runner normalizes outputs into `.debt/latest_items.json` and writes a readable `.debt/latest_summary.md`.

## Quick start

From the root of your target repository, copy this scaffold into the repo first, then run the installer for your platform. The installer will install `uv` if it is missing, then use `uv tool install` for Ruff, mypy, Bandit, Semgrep, and pre-commit.

macOS / Linux / WSL / Git Bash:

```bash
bash scripts/install-debt-harness.sh
python3 scripts/debt_scan.py scan
python3 scripts/debt_scan.py baseline
```

Native Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-debt-harness.ps1
py -3 .\scripts\debt_scan.py scan
py -3 .\scripts\debt_scan.py baseline
```

PowerShell 7 also works:

```powershell
pwsh -File .\scripts\install-debt-harness.ps1
py -3 .\scripts\debt_scan.py scan
```

After you have a baseline, use this in CI:

```bash
python3 scripts/debt_scan.py scan --ci
```

On Windows CI, use:

```powershell
py -3 .\scripts\debt_scan.py scan --ci
```

`--ci` compares the latest scan against `.debt/baseline.json` and fails when configured gates are violated.

## Recommended repo layout after installation

```text
.debt-harness.json                 # harness config and gates
.debt/                             # generated scan output and baseline
scripts/debt_scan.py               # scanner, normalizer, scorer
scripts/install-debt-harness.sh    # macOS/Linux/WSL/Git Bash installer
scripts/install-debt-harness.ps1   # native Windows PowerShell installer
eslint.config.mjs                  # TS/JS lint config if missing
ruff.toml                          # Python lint config if missing
.clang-tidy                        # C/C++ clang-tidy config if missing
cppcheck-suppressions.txt          # cppcheck suppressions if missing
.semgrep.yml                       # local Semgrep rules if missing
.pre-commit-config.yaml            # optional local hooks
docs/tech-debt-tracker.md          # human-facing ledger
docs/tech-debt-policy.md           # severity and paydown policy
docs/tech-debt-review-template.md  # written-review output template
.claude/skills/tech-debt-static-analysis/SKILL.md  # Claude Code skill
```

## How the scoring works

Each finding receives:

- `principal_minutes`: rough time to fix the issue.
- `monthly_interest_minutes`: expected recurring friction per month.
- `fingerprint`: stable-ish identifier for baselining.

The default monthly interest model is:

```text
monthly_interest = principal_minutes × severity_rate × exposure_factor
exposure_factor = 1 + min(file_churn_90d / 8, 2)
```

The scanner derives `file_churn_90d` from Git history when available. Hot files therefore accumulate more interest than cold files.

This is deliberately approximate. The value is in consistent trend tracking: whether high-interest debt is going down, whether new code introduces new debt, and which hotspots deserve refactoring.

## Typical workflow

1. Run `python3 scripts/debt_scan.py scan` locally or in CI.
2. Review `.debt/latest_summary.md`.
3. Convert the top findings into explicit debt items in `docs/tech-debt-tracker.md`.
4. Fix high-interest issues first.
5. Re-run the scan and refresh the baseline only after intentional acceptance.
6. Add project-specific Semgrep rules or clang-tidy checks for recurring review comments.

## Commands

macOS / Linux / WSL:

```bash
python3 scripts/debt_scan.py scan
python3 scripts/debt_scan.py scan --tools ruff,mypy,eslint,clippy
python3 scripts/debt_scan.py scan --ci
python3 scripts/debt_scan.py baseline
python3 scripts/debt_scan.py compare
python3 scripts/debt_scan.py doctor
```

Windows:

```powershell
py -3 .\scripts\debt_scan.py scan
py -3 .\scripts\debt_scan.py scan --tools ruff,mypy,eslint,clippy
py -3 .\scripts\debt_scan.py scan --ci
py -3 .\scripts\debt_scan.py baseline
py -3 .\scripts\debt_scan.py compare
py -3 .\scripts\debt_scan.py doctor
```

## Windows notes

- `scripts/install-debt-harness.sh` is not a native Windows script. Use it under WSL, Git Bash, or MSYS2.
- `scripts/install-debt-harness.ps1` is the native Windows installer.
- The PowerShell installer uses `uv tool` for Python CLI tools. If `uv` is missing, it tries to install uv with `winget`, then Scoop, then the official standalone installer. For non-Python system tools such as Node.js, Cppcheck, LLVM/clang-tidy, and Rustup, it tries `winget`, then Chocolatey, then Scoop.
- Some Windows installers update `PATH` only for new terminals. If `doctor` still cannot find `uv`, `ruff`, `semgrep`, `clang-tidy`, `npm`, or `rustup` immediately after installation, reopen PowerShell and rerun `py -3 .\scripts\debt_scan.py doctor`.
- The scanner itself is Python standard library only and is designed to run on Windows, macOS, Linux, WSL, and CI.

## Notes

- C/C++ clang-tidy works best when `compile_commands.json` exists. For CMake projects, configure with `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`. Set `tools.clang_tidy.requires_compile_commands: false` in `.debt-harness.json` to attempt clang-tidy without one.
- `cppcheck` can run without a compile database, but results are better with one. Both `cppcheck` and `clang_tidy` commands are built dynamically by default; set `tools.<name>.command` in `.debt-harness.json` to override.
- Semgrep defaults to local rules from `.semgrep.yml` in this scaffold. Switch the command in `.debt-harness.json` if you want `semgrep scan --config auto` or managed scans.
- For Rust subprojects (e.g. Tauri's `src-tauri/`), the harness auto-discovers `Cargo.toml` up to 2 levels deep. Set `tools.clippy.cwd` / `tools.cargo_audit.cwd` in `.debt-harness.json` to pin a specific directory.
- The scanner treats analyzer non-zero exit codes as expected, because linters usually exit non-zero when findings are present.
- The scoring defaults are intentionally conservative. Tune `.debt-harness.json` after two or three weeks of real data.

## Windows note: cargo-audit

The Windows installer checks for the `cargo-audit` executable before invoking `cargo audit`. If `cargo install cargo-audit --locked` succeeds but `doctor` still cannot find it, reopen PowerShell or add Cargo's bin directory, usually `$env:USERPROFILE\.cargo\bin`, to `PATH`.

### Windows encoding note

On Windows, some scanners emit UTF-8 while the default console code page may be GBK/CP936.
`debt_scan.py` forces subprocess decoding to UTF-8 with replacement and sets `PYTHONUTF8=1` for child Python tools.
If you still see garbled output from a tool, run PowerShell with:

```powershell
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
py -3 .\scripts\debt_scan.py scan
```

