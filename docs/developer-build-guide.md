# Developer Build & Version Guide

## How Versioning Works

Version must be consistent across two files:

    ┌─────────────────────┐     ┌──────────┐
    │  pyproject.toml     │     │ VERSION  │
    │  version = "0.2.0"  │ ══> │ 0.2.0    │
    └─────────────────────┘     └──────────┘
            │                        │
            │    Must match          │
            └────────┬───────────────┘
                     │
              ┌──────▼──────┐
              │  git tag    │
              │  v0.2.0     │
              └─────────────┘

Files to update when bumping version:

| File             | Field              | Example   |
|------------------|--------------------|-----------|
| `pyproject.toml` | `project.version`  | `"0.3.0"` |
| `VERSION`        | entire file content | `0.3.0`   |

At runtime, the app resolves version in this order:

1. `KLIN_APP_VERSION` env var (if set)
2. `VERSION` file (bundled in binary or repo root)
3. `pyproject.toml` project.version
4. Fallback: `0.0.0`

## Section 1: Local Build

### Prerequisites

- Python 3.13
- uv (package manager)
- Bash shell (default on macOS/Linux; use Git Bash or WSL on Windows)
- Rust toolchain (only needed if auto-detecting target triple for Tauri copy)

### Step 1 — Install dependencies

```bash
uv sync --extra dev --extra build
```

### Step 2 — Run quality checks (optional but recommended)

```bash
# Version sync check
uv run python scripts/check_version_sync.py

# Lint
uv run ruff check app tests scripts main.py --select E9,F63,F7,F82

# Format check
uv run ruff format --check tests scripts

# Tests (skip LLM-dependent tests)
uv run pytest -q -m "not llm"
```

### Step 3 — Build the sidecar binary

#### Production mode (CI-like output)

```bash
# Build and copy to Tauri app (default)
./scripts/build-sidecar.sh --mode production

# Build only, no Tauri copy (standalone test)
./scripts/build-sidecar.sh --mode production --skip-tauri-copy

# Specify a custom target triple
./scripts/build-sidecar.sh --mode production --target-triple aarch64-apple-darwin
```

#### Dev mode with tracing (debug PyInstaller failures)

```bash
# Verbose build with shell tracing
./scripts/build-sidecar.sh --mode dev --trace
```

#### Windows

Run the same commands in Git Bash or WSL.

### Step 4 — Verify the binary

```bash
# macOS / Linux
./dist/klin-worker --help
```

```powershell
# Windows
.\dist\klin-worker.exe --help
```

### Build output

| OS      | Binary path            |
|---------|------------------------|
| macOS   | `dist/klin-worker`     |
| Linux   | `dist/klin-worker`     |
| Windows | `dist\klin-worker.exe` |

When Tauri copy is enabled, the binary is also placed at:
`../klin-app/src-tauri/binaries/klin-worker-<target-triple>[.exe]`

Each build also writes metadata JSON (default path: `dist/build-metadata.json`) including mode, size in bytes, SHA256, target triple, and tool versions.

## Section 2: CI/CD Pipeline

### Visual Flow

```
Push/PR to main or dev          Push tag v0.3.0
        │                              │
        ▼                              ▼
  ┌───────────┐               ┌──────────────┐
  │  CI       │               │ Quality Gate │
  │ Workflow  │               │ (same as CI) │
  └─────┬─────┘               └──────┬───────┘
        │                             │
        ▼                             ▼
  ┌───────────┐               ┌──────────────┐
  │ Lint      │               │ Validate Tag │
  │ + Test    │               │ vX.Y.Z check │
  │ + Version │               └──────┬───────┘
  │   Sync    │                      │
  └───────────┘                      ▼
                              ┌──────────────┐
       Done                   │ Build Matrix │
                              │ 4 platforms  │
                              └──────┬───────┘
                                     │
                              ┌──────┴──────────────────┐
                              │         │        │      │
                              ▼         ▼        ▼      ▼
                           macOS     macOS    Windows  Linux
                           ARM64    Intel     x64      x64
                              │         │        │      │
                              └──────┬──────────────────┘
                                     │
                                     ▼
                              ┌──────────────┐
                              │ Publish      │
                              │ GitHub       │
                              │ Release      │
                              └──────────────┘
```

### CI Workflow (automatic on every push/PR)

**Trigger:** push or PR to `main` or `dev`

What it checks:

1. Install deps with `uv sync --extra dev`
2. Version consistency (`pyproject.toml` == `VERSION`)
3. Ruff lint (syntax/runtime errors)
4. Ruff format (tests and scripts)
5. Pytest (non-LLM tests only)

No action needed — this runs automatically.

### Release Workflow (step by step)

**Trigger:** push a git tag matching `vX.Y.Z`

#### Step 1 — Bump version in both files

Edit these two files:

```
pyproject.toml  →  version = "0.3.0"
VERSION         →  0.3.0
```

#### Step 2 — Commit the version bump

```bash
git add pyproject.toml VERSION
git commit -m "chore: bump version to 0.3.0"
```

#### Step 3 — Push the commit

```bash
git push origin dev       # or main, whichever branch you release from
```

#### Step 4 — Wait for CI to pass

Check the Actions tab on GitHub. The CI workflow must pass before proceeding.

#### Step 5 — Create and push the tag

```bash
git tag v0.3.0
git push origin v0.3.0
```

This triggers the release workflow:

1. **Quality Gate** — runs the same lint + test as CI
2. **Validate Tag** — confirms `v0.3.0` matches `pyproject.toml` and `VERSION`
3. **Build** — produces binaries on 4 platforms:

| Platform         | Artifact name                                  |
|------------------|------------------------------------------------|
| macOS ARM64      | `klin-worker-0.3.0-aarch64-apple-darwin`       |
| macOS Intel      | `klin-worker-0.3.0-x86_64-apple-darwin`        |
| Windows x64      | `klin-worker-0.3.0-x86_64-pc-windows-msvc.exe` |
| Linux x64        | `klin-worker-0.3.0-x86_64-unknown-linux-gnu`   |

4. **Publish** — uploads all binaries + SHA256 checksums to a GitHub Release

#### Step 6 — Verify the release

Go to GitHub Releases page. Download artifacts and verify checksums:

**macOS / Linux:**

```bash
sha256sum -c SHA256SUMS.txt
```

**Windows (PowerShell):**

```powershell
Get-Content SHA256SUMS.txt | ForEach-Object {
    $parts = $_ -split "  "
    $computed = (Get-FileHash $parts[1] -Algorithm SHA256).Hash.ToLower()
    if ($computed -eq $parts[0]) { "OK: $($parts[1])" } else { "FAIL: $($parts[1])" }
}
```

### Rollback

Do NOT overwrite existing releases. Create a new patch version instead:

```bash
# Fix the issue, then:
git add .
git commit -m "fix: <description>"
git tag v0.3.1
git push origin dev && git push origin v0.3.1
```
