# Install static-analysis tools and copy non-destructive default configs into a repo.
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/install-debt-harness.ps1 [-Repo C:\path\to\repo] [-InstallHooks] [-NoCopyConfigs] [-NoSystemTools]
#   pwsh -File scripts/install-debt-harness.ps1 [-Repo C:\path\to\repo] [-InstallHooks] [-NoCopyConfigs] [-NoSystemTools]

[CmdletBinding()]
param(
    [string]$Repo = "",
    [switch]$InstallHooks,
    [switch]$NoCopyConfigs,
    [switch]$NoSystemTools
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$HarnessDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Write-Log {
    param([string]$Message)
    Write-Host "[debt-harness] $Message" -ForegroundColor Cyan
}

function Write-Warn {
    param([string]$Message)
    Write-Warning "[debt-harness] $Message"
}

function Test-Command {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-External {
    param(
        [string]$Exe,
        [string[]]$ExternalArgs,
        [switch]$IgnoreFailure
    )
    & $Exe @ExternalArgs
    $code = $LASTEXITCODE
    if ($code -ne 0 -and -not $IgnoreFailure) {
        throw "Command failed with exit code ${code}: $Exe $($ExternalArgs -join ' ')"
    }
    return $code
}

function Get-RepoRoot {
    param([string]$RequestedRepo)
    if (-not [string]::IsNullOrWhiteSpace($RequestedRepo)) {
        return (Resolve-Path $RequestedRepo).Path
    }
    if (Test-Command git) {
        $gitRoot = (& git rev-parse --show-toplevel 2>$null)
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($gitRoot)) {
            return (Resolve-Path $gitRoot.Trim()).Path
        }
    }
    return (Get-Location).Path
}

$RepoRoot = Get-RepoRoot -RequestedRepo $Repo
Write-Log "repo root: $RepoRoot"

function Get-RelativePathSafe {
    param([string]$Path)
    try {
        return [System.IO.Path]::GetRelativePath($RepoRoot, $Path)
    } catch {
        return $Path
    }
}

function Copy-IfMissing {
    param(
        [string]$Src,
        [string]$Dst
    )
    if (Test-Path -LiteralPath $Dst) {
        Write-Log "keep existing $(Get-RelativePathSafe $Dst)"
        return
    }
    $parent = Split-Path -Parent $Dst
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    Copy-Item -LiteralPath $Src -Destination $Dst -Force
    Write-Log "created $(Get-RelativePathSafe $Dst)"
}

$ExcludedDirRegex = '([\\/])(.git|node_modules|target|build|dist|.debt|.venv|venv|env|.mypy_cache|.ruff_cache|__pycache__)([\\/])'

function Test-HasFiles {
    param([string[]]$Patterns)
    try {
        $match = Get-ChildItem -Path $RepoRoot -Recurse -File -Include $Patterns -ErrorAction SilentlyContinue |
            Where-Object {
                $_.FullName -notmatch $ExcludedDirRegex -and
                $_.FullName -notmatch '([\\/])scripts([\\/])debt_scan\.py$'
            } |
            Select-Object -First 1
        return $null -ne $match
    } catch {
        Write-Warn "file detection failed for patterns $($Patterns -join ', '): $($_.Exception.Message)"
        return $false
    }
}

function Invoke-PythonModule {
    param([string[]]$PythonArgs)
    if (Test-Command py) {
        & py -3 @PythonArgs
        return $LASTEXITCODE
    }
    if (Test-Command python) {
        & python @PythonArgs
        return $LASTEXITCODE
    }
    if (Test-Command python3) {
        & python3 @PythonArgs
        return $LASTEXITCODE
    }
    throw "Python was not found. Install Python 3 and rerun this script."
}

function Add-UvPathsToCurrentSession {
    $candidateDirs = @(
        (Join-Path $HOME '.local\bin'),
        (Join-Path $HOME '.cargo\bin')
    )
    if ($env:USERPROFILE) {
        $candidateDirs += (Join-Path $env:USERPROFILE '.local\bin')
        $candidateDirs += (Join-Path $env:USERPROFILE '.cargo\bin')
    }
    if ($env:LOCALAPPDATA) {
        $candidateDirs += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Scripts')
    }
    foreach ($dir in $candidateDirs | Select-Object -Unique) {
        if ((Test-Path -LiteralPath $dir) -and ($env:PATH -notlike "*$dir*")) {
            $env:PATH = "$dir;$env:PATH"
        }
    }
}

function Ensure-Uv {
    if (Test-Command uv) {
        Write-Log 'uv already installed'
        return $true
    }

    Write-Log 'installing uv'
    if ((Test-Command 'winget')) {
        & winget install --id astral-sh.uv --exact --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0) { Write-Warn 'winget install failed for uv' }
    } elseif ((Test-Command 'scoop')) {
        & scoop install main/uv
        if ($LASTEXITCODE -ne 0) { Write-Warn 'scoop install failed for uv' }
    } else {
        powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
        if ($LASTEXITCODE -ne 0) { Write-Warn 'standalone uv installer failed' }
    }

    Add-UvPathsToCurrentSession
    if (Test-Command uv) {
        & uv tool update-shell *> $null
        return $true
    }

    Write-Warn 'uv is still not on PATH. Reopen PowerShell or install uv manually.'
    return $false
}

function Install-PythonTool {
    param(
        [string]$Tool,
        [string]$Package = $Tool
    )

    if (-not (Ensure-Uv)) { return }

    if (Test-Command $Tool) {
        Write-Log "$Tool already available on PATH"
        return
    }

    if ($Package -eq 'semgrep') {
        $env:PYTHONUTF8 = '1'
        try { [System.Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'User') } catch { }
    }

    Write-Log "installing $Package via uv tool"
    & uv tool install $Package
    if ($LASTEXITCODE -ne 0) {
        & uv tool upgrade $Package
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "uv tool install/upgrade failed for $Package"
        }
    }
    Add-UvPathsToCurrentSession
}

function Install-WindowsPackage {
    param(
        [string]$Name,
        [string]$WingetId = '',
        [string]$ChocoPackage = '',
        [string]$ScoopPackage = ''
    )

    if ($NoSystemTools) {
        Write-Warn "system package install disabled; please install $Name manually"
        return
    }

    if ((Test-Command 'winget') -and -not [string]::IsNullOrWhiteSpace($WingetId)) {
        Write-Log "installing $Name via winget"
        & winget install --id $WingetId --exact --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -eq 0) { return }
        Write-Warn "winget install failed for $Name"
    }

    if ((Test-Command 'choco') -and -not [string]::IsNullOrWhiteSpace($ChocoPackage)) {
        Write-Log "installing $Name via Chocolatey"
        & choco install -y $ChocoPackage
        if ($LASTEXITCODE -eq 0) { return }
        Write-Warn "Chocolatey install failed for $Name"
    }

    if ((Test-Command 'scoop') -and -not [string]::IsNullOrWhiteSpace($ScoopPackage)) {
        Write-Log "installing $Name via Scoop"
        & scoop install $ScoopPackage
        if ($LASTEXITCODE -eq 0) { return }
        Write-Warn "Scoop install failed for $Name"
    }

    Write-Warn "no supported package manager installed or package install failed; please install $Name manually"
}

if (-not $NoCopyConfigs) {
    Copy-IfMissing (Join-Path $HarnessDir 'configs\debt-harness.json') (Join-Path $RepoRoot '.debt-harness.json')
    Copy-IfMissing (Join-Path $HarnessDir 'scripts\debt_scan.py') (Join-Path $RepoRoot 'scripts\debt_scan.py')
    Copy-IfMissing (Join-Path $HarnessDir 'scripts\install-debt-harness.sh') (Join-Path $RepoRoot 'scripts\install-debt-harness.sh')
    Copy-IfMissing (Join-Path $HarnessDir 'scripts\install-debt-harness.ps1') (Join-Path $RepoRoot 'scripts\install-debt-harness.ps1')
    Copy-IfMissing (Join-Path $HarnessDir 'docs\tech-debt-tracker.md') (Join-Path $RepoRoot 'docs\tech-debt-tracker.md')
    Copy-IfMissing (Join-Path $HarnessDir 'docs\tech-debt-policy.md') (Join-Path $RepoRoot 'docs\tech-debt-policy.md')
    Copy-IfMissing (Join-Path $HarnessDir 'docs\tech-debt-review-template.md') (Join-Path $RepoRoot 'docs\tech-debt-review-template.md')
    Copy-IfMissing (Join-Path $HarnessDir 'AGENTS.md') (Join-Path $RepoRoot 'AGENTS.md')
    Copy-IfMissing (Join-Path $HarnessDir 'skills\tech-debt-static-analysis\SKILL.md') (Join-Path $RepoRoot '.claude\skills\tech-debt-static-analysis\SKILL.md')

    if (-not ((Test-Path (Join-Path $RepoRoot 'eslint.config.js')) -or (Test-Path (Join-Path $RepoRoot 'eslint.config.mjs')) -or (Test-Path (Join-Path $RepoRoot 'eslint.config.cjs')))) {
        Copy-IfMissing (Join-Path $HarnessDir 'configs\eslint.config.mjs') (Join-Path $RepoRoot 'eslint.config.mjs')
    }
    if (-not (Test-Path (Join-Path $RepoRoot 'tsconfig.debt.json'))) {
        Copy-IfMissing (Join-Path $HarnessDir 'configs\tsconfig.debt.json') (Join-Path $RepoRoot 'tsconfig.debt.json')
    }
    if (-not ((Test-Path (Join-Path $RepoRoot 'ruff.toml')) -or (Test-Path (Join-Path $RepoRoot '.ruff.toml')))) {
        Copy-IfMissing (Join-Path $HarnessDir 'configs\ruff.toml') (Join-Path $RepoRoot 'ruff.toml')
    }
    if (-not (Test-Path (Join-Path $RepoRoot '.clang-tidy'))) {
        Copy-IfMissing (Join-Path $HarnessDir 'configs\.clang-tidy') (Join-Path $RepoRoot '.clang-tidy')
    }
    if (-not (Test-Path (Join-Path $RepoRoot 'cppcheck-suppressions.txt'))) {
        Copy-IfMissing (Join-Path $HarnessDir 'configs\cppcheck-suppressions.txt') (Join-Path $RepoRoot 'cppcheck-suppressions.txt')
    }
    if (-not (Test-Path (Join-Path $RepoRoot 'clippy.toml'))) {
        Copy-IfMissing (Join-Path $HarnessDir 'configs\clippy.toml') (Join-Path $RepoRoot 'clippy.toml')
    }
    if (-not (Test-Path (Join-Path $RepoRoot '.semgrep.yml'))) {
        Copy-IfMissing (Join-Path $HarnessDir 'configs\semgrep.yml') (Join-Path $RepoRoot '.semgrep.yml')
    }
    if (-not (Test-Path (Join-Path $RepoRoot '.pre-commit-config.yaml'))) {
        Copy-IfMissing (Join-Path $HarnessDir 'configs\pre-commit-config.yaml') (Join-Path $RepoRoot '.pre-commit-config.yaml')
    }
    if (-not (Test-Path (Join-Path $RepoRoot '.github\workflows\tech-debt.yml'))) {
        Copy-IfMissing (Join-Path $HarnessDir 'configs\github-actions-tech-debt.yml') (Join-Path $RepoRoot '.github\workflows\tech-debt.yml')
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot '.debt\results') | Out-Null
}

# Python ecosystem tools.
if ((Test-HasFiles @('*.py', '*.pyi')) -or (Test-Path (Join-Path $RepoRoot 'pyproject.toml')) -or (Test-Path (Join-Path $RepoRoot 'requirements.txt'))) {
    Install-PythonTool -Tool 'ruff' -Package 'ruff'
    Install-PythonTool -Tool 'mypy' -Package 'mypy'
    Install-PythonTool -Tool 'bandit' -Package 'bandit'
}

# Cross-language policy scanning and local hooks.
Install-PythonTool -Tool 'semgrep' -Package 'semgrep'
Install-PythonTool -Tool 'pre-commit' -Package 'pre-commit'

# TypeScript ecosystem tools. Keep them local to the repo when package.json exists.
if ((Test-HasFiles @('*.ts', '*.tsx', '*.js', '*.jsx', '*.mts', '*.cts')) -or (Test-Path (Join-Path $RepoRoot 'tsconfig.json')) -or (Test-Path (Join-Path $RepoRoot 'package.json'))) {
    if (-not (Test-Command npm)) {
        Install-WindowsPackage -Name 'Node.js LTS' -WingetId 'OpenJS.NodeJS.LTS' -ChocoPackage 'nodejs-lts' -ScoopPackage 'nodejs-lts'
    }
    if (Test-Command npm) {
        Push-Location $RepoRoot
        try {
            if (-not (Test-Path (Join-Path $RepoRoot 'package.json'))) {
                Write-Warn 'TypeScript files found but no package.json; creating a minimal private package.json for analyzer devDependencies'
                '{"private":true,"devDependencies":{}}' | Set-Content -Path (Join-Path $RepoRoot 'package.json') -Encoding UTF8
            }
            Write-Log 'installing TypeScript analyzer devDependencies'
            & npm install --save-dev eslint typescript typescript-eslint '@eslint/js'
            if ($LASTEXITCODE -ne 0) { Write-Warn 'npm install failed' }
        } finally {
            Pop-Location
        }
    } else {
        Write-Warn 'npm not found; install Node.js/npm before running ESLint or tsc. You may need to reopen the terminal after package installation.'
    }
}

# C/C++ tools.
if ((Test-HasFiles @('*.c', '*.cc', '*.cpp', '*.cxx', '*.h', '*.hh', '*.hpp', '*.hxx')) -or (Test-Path (Join-Path $RepoRoot 'compile_commands.json')) -or (Test-Path (Join-Path $RepoRoot 'CMakeLists.txt'))) {
    if (-not (Test-Command cppcheck)) {
        Install-WindowsPackage -Name 'Cppcheck' -WingetId 'Cppcheck.Cppcheck' -ChocoPackage 'cppcheck' -ScoopPackage 'cppcheck'
    }
    if (-not (Test-Command clang-tidy)) {
        Install-WindowsPackage -Name 'LLVM / clang-tidy' -WingetId 'LLVM.LLVM' -ChocoPackage 'llvm' -ScoopPackage 'llvm'
        if (-not (Test-Command clang-tidy)) {
            Write-Warn 'clang-tidy was not found on PATH. If LLVM was just installed, reopen PowerShell or add the LLVM bin directory to PATH.'
        }
    }
}

# Rust tools.
if ((Test-Path (Join-Path $RepoRoot 'Cargo.toml')) -or (Test-HasFiles @('*.rs'))) {
    if (-not (Test-Command rustup)) {
        Install-WindowsPackage -Name 'Rustup' -WingetId 'Rustlang.Rustup' -ChocoPackage 'rustup.install' -ScoopPackage 'rustup'
    }
    if (Test-Command rustup) {
        Write-Log 'adding rustfmt and clippy components'
        & rustup component add rustfmt clippy
        if ($LASTEXITCODE -ne 0) { Write-Warn 'rustup component add failed' }
    } else {
        Write-Warn 'rustup not found; install Rust via rustup to use cargo clippy'
    }
    if (Test-Command cargo) {
        # cargo-audit is distributed as the cargo-audit executable.
        # After installation Cargo also exposes it as `cargo audit`, but calling
        # `cargo audit --version` before it exists makes PowerShell surface a
        # NativeCommandError. Check the executable first, then install if needed.
        if (-not (Test-Command cargo-audit)) {
            Write-Log 'installing cargo-audit'
            & cargo install cargo-audit --locked
            if ($LASTEXITCODE -ne 0) {
                Write-Warn 'cargo-audit installation failed'
            } elseif (-not (Test-Command cargo-audit)) {
                Write-Warn 'cargo-audit was installed but is not on PATH yet. Reopen PowerShell or add ~/.cargo/bin to PATH.'
            }
        }
    }
}

if ($InstallHooks) {
    Push-Location $RepoRoot
    try {
        if (Test-Command pre-commit) {
            & pre-commit install --hook-type pre-commit --hook-type pre-push
            if ($LASTEXITCODE -ne 0) { Write-Warn 'pre-commit hook installation failed' }
        } else {
            Write-Warn 'pre-commit not found; hooks not installed. You may need to reopen the terminal after uv tool installation.'
        }
    } finally {
        Pop-Location
    }
}

Write-Log 'installation complete'
Write-Log 'next: py -3 scripts\debt_scan.py doctor; py -3 scripts\debt_scan.py scan'
