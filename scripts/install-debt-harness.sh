#!/usr/bin/env bash
set -euo pipefail

# Install static-analysis tools and copy non-destructive default configs into a repo.
# Usage:
#   bash scripts/install-debt-harness.sh [--repo /path/to/repo] [--install-hooks] [--no-copy-configs]

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
COPY_CONFIGS=1
INSTALL_HOOKS=0
INSTALL_SYSTEM_TOOLS=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      REPO_ROOT="$2"
      shift 2
      ;;
    --install-hooks)
      INSTALL_HOOKS=1
      shift
      ;;
    --no-copy-configs)
      COPY_CONFIGS=0
      shift
      ;;
    --no-system-tools)
      INSTALL_SYSTEM_TOOLS=0
      shift
      ;;
    -h|--help)
      sed -n '1,40p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"

log() { printf '\033[1;34m[debt-harness]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[debt-harness:warn]\033[0m %s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

copy_if_missing() {
  local src="$1"
  local dst="$2"
  if [[ -e "$dst" ]]; then
    log "keep existing $(realpath --relative-to="$REPO_ROOT" "$dst" 2>/dev/null || echo "$dst")"
  else
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    log "created $(realpath --relative-to="$REPO_ROOT" "$dst" 2>/dev/null || echo "$dst")"
  fi
}

has_files() {
  local pattern="$1"
  find "$REPO_ROOT" \
    \( -name .git -o -name node_modules -o -name target -o -name build -o -name .debt -o -name .venv -o -name dist \) -prune \
    -o -name "$pattern" -type f ! -path "$REPO_ROOT/scripts/debt_scan.py" -print -quit | grep -q .
}

ensure_uv() {
  if have uv; then
    log "uv already installed"
  else
    log "installing uv"
    if have brew; then
      brew install uv || true
    elif have curl; then
      curl -LsSf https://astral.sh/uv/install.sh | sh || true
    elif have wget; then
      wget -qO- https://astral.sh/uv/install.sh | sh || true
    else
      warn "uv not found and neither curl nor wget is available; install uv manually"
    fi
  fi

  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  if ! have uv; then
    warn "uv is still not on PATH; reopen your shell or install uv manually"
    return 1
  fi
  uv tool update-shell >/dev/null 2>&1 || true
}

install_python_tool() {
  local tool="$1"
  local pkg="${2:-$1}"
  ensure_uv || return

  if have "$tool"; then
    log "$tool already available on PATH"
    return
  fi

  log "installing $pkg via uv tool"
  uv tool install "$pkg" || uv tool upgrade "$pkg" || warn "uv tool install/upgrade failed for $pkg"
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
}

install_system_pkg() {
  local pkg="$1"
  if [[ "$INSTALL_SYSTEM_TOOLS" -eq 0 ]]; then
    warn "system package install disabled; please install $pkg manually"
    return
  fi

  if have brew; then
    log "installing $pkg via brew"
    brew install "$pkg" || true
  elif have apt-get; then
    log "installing $pkg via apt-get"
    sudo apt-get update -y
    sudo apt-get install -y "$pkg" || true
  elif have dnf; then
    log "installing $pkg via dnf"
    sudo dnf install -y "$pkg" || true
  elif have pacman; then
    log "installing $pkg via pacman"
    sudo pacman -Sy --noconfirm "$pkg" || true
  else
    warn "no supported package manager found; please install $pkg manually"
  fi
}

log "repo root: $REPO_ROOT"

if [[ "$COPY_CONFIGS" -eq 1 ]]; then
  copy_if_missing "$HARNESS_DIR/configs/debt-harness.json" "$REPO_ROOT/.debt-harness.json"
  copy_if_missing "$HARNESS_DIR/scripts/debt_scan.py" "$REPO_ROOT/scripts/debt_scan.py"
  chmod +x "$REPO_ROOT/scripts/debt_scan.py" || true
  copy_if_missing "$HARNESS_DIR/scripts/install-debt-harness.sh" "$REPO_ROOT/scripts/install-debt-harness.sh"
  chmod +x "$REPO_ROOT/scripts/install-debt-harness.sh" || true
  copy_if_missing "$HARNESS_DIR/scripts/install-debt-harness.ps1" "$REPO_ROOT/scripts/install-debt-harness.ps1"
  copy_if_missing "$HARNESS_DIR/docs/tech-debt-tracker.md" "$REPO_ROOT/docs/tech-debt-tracker.md"
  copy_if_missing "$HARNESS_DIR/docs/tech-debt-policy.md" "$REPO_ROOT/docs/tech-debt-policy.md"
  copy_if_missing "$HARNESS_DIR/docs/tech-debt-review-template.md" "$REPO_ROOT/docs/tech-debt-review-template.md"
  copy_if_missing "$HARNESS_DIR/AGENTS.md" "$REPO_ROOT/AGENTS.md"
  copy_if_missing "$HARNESS_DIR/skills/tech-debt-static-analysis/SKILL.md" "$REPO_ROOT/.claude/skills/tech-debt-static-analysis/SKILL.md"

  if [[ ! -e "$REPO_ROOT/eslint.config.js" && ! -e "$REPO_ROOT/eslint.config.mjs" && ! -e "$REPO_ROOT/eslint.config.cjs" ]]; then
    copy_if_missing "$HARNESS_DIR/configs/eslint.config.mjs" "$REPO_ROOT/eslint.config.mjs"
  fi
  [[ -e "$REPO_ROOT/tsconfig.debt.json" ]] || copy_if_missing "$HARNESS_DIR/configs/tsconfig.debt.json" "$REPO_ROOT/tsconfig.debt.json"
  [[ -e "$REPO_ROOT/ruff.toml" || -e "$REPO_ROOT/.ruff.toml" ]] || copy_if_missing "$HARNESS_DIR/configs/ruff.toml" "$REPO_ROOT/ruff.toml"
  [[ -e "$REPO_ROOT/.clang-tidy" ]] || copy_if_missing "$HARNESS_DIR/configs/.clang-tidy" "$REPO_ROOT/.clang-tidy"
  [[ -e "$REPO_ROOT/cppcheck-suppressions.txt" ]] || copy_if_missing "$HARNESS_DIR/configs/cppcheck-suppressions.txt" "$REPO_ROOT/cppcheck-suppressions.txt"
  [[ -e "$REPO_ROOT/clippy.toml" ]] || copy_if_missing "$HARNESS_DIR/configs/clippy.toml" "$REPO_ROOT/clippy.toml"
  [[ -e "$REPO_ROOT/.semgrep.yml" ]] || copy_if_missing "$HARNESS_DIR/configs/semgrep.yml" "$REPO_ROOT/.semgrep.yml"
  [[ -e "$REPO_ROOT/.pre-commit-config.yaml" ]] || copy_if_missing "$HARNESS_DIR/configs/pre-commit-config.yaml" "$REPO_ROOT/.pre-commit-config.yaml"
  [[ -e "$REPO_ROOT/.github/workflows/tech-debt.yml" ]] || copy_if_missing "$HARNESS_DIR/configs/github-actions-tech-debt.yml" "$REPO_ROOT/.github/workflows/tech-debt.yml"
  mkdir -p "$REPO_ROOT/.debt/results"
fi

# Python ecosystem tools.
if has_files '*.py' || [[ -e "$REPO_ROOT/pyproject.toml" || -e "$REPO_ROOT/requirements.txt" ]]; then
  install_python_tool ruff ruff
  install_python_tool mypy mypy
  install_python_tool bandit bandit
fi

# Cross-language policy scanning.
install_python_tool semgrep semgrep
install_python_tool pre-commit pre-commit

# TypeScript ecosystem tools. Keep them local to the repo when package.json exists.
if has_files '*.ts' || has_files '*.tsx' || [[ -e "$REPO_ROOT/tsconfig.json" || -e "$REPO_ROOT/package.json" ]]; then
  if have npm; then
    pushd "$REPO_ROOT" >/dev/null
    if [[ ! -e package.json ]]; then
      warn "TypeScript files found but no package.json; creating a minimal private package.json for analyzer devDependencies"
      cat > package.json <<'PKG'
{"private":true,"devDependencies":{}}
PKG
    fi
    log "installing TypeScript analyzer devDependencies"
    npm install --save-dev eslint typescript typescript-eslint @eslint/js
    popd >/dev/null
  else
    warn "npm not found; install Node.js/npm before running ESLint or tsc"
  fi
fi

# C/C++ tools.
if has_files '*.c' || has_files '*.cc' || has_files '*.cpp' || has_files '*.cxx' || has_files '*.h' || has_files '*.hpp'; then
  have cppcheck || install_system_pkg cppcheck
  if ! have clang-tidy; then
    if [[ "$OS" == "darwin" ]]; then
      install_system_pkg llvm
      warn "Homebrew llvm is often keg-only. Add /opt/homebrew/opt/llvm/bin or /usr/local/opt/llvm/bin to PATH if clang-tidy is not found."
    else
      install_system_pkg clang-tidy
    fi
  fi
fi

# Rust tools.
if [[ -e "$REPO_ROOT/Cargo.toml" ]] || has_files '*.rs'; then
  if have rustup; then
    log "adding rustfmt and clippy components"
    rustup component add rustfmt clippy || true
  else
    warn "rustup not found; install Rust via rustup to use cargo clippy"
  fi
  if have cargo; then
    if ! command -v cargo-audit >/dev/null 2>&1; then
      log "installing cargo-audit"
      cargo install cargo-audit --locked || warn "cargo-audit installation failed"
    fi
  fi
fi

if [[ "$INSTALL_HOOKS" -eq 1 ]]; then
  pushd "$REPO_ROOT" >/dev/null
  if have pre-commit; then
    pre-commit install --hook-type pre-commit --hook-type pre-push
  else
    warn "pre-commit not found; hooks not installed"
  fi
  popd >/dev/null
fi

log "installation complete"
log "next: python3 scripts/debt_scan.py doctor && python3 scripts/debt_scan.py scan"
