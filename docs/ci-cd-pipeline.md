# CI/CD Pipeline

This document defines the GitHub Actions CI/CD pipeline for Klin-Worker, a FastAPI sidecar packaged as an executable for Tauri.

## Goals

- Fast and deterministic PR feedback.
- Version-tag-driven releases.
- Multi-platform executable assets for Tauri sidecar distribution.
- Safe defaults: no required live llama-server for standard CI.

## Workflows

- CI workflow: .github/workflows/ci.yml
- Release workflow: .github/workflows/release-sidecar.yml

## CI Workflow

Trigger:

- push to main and dev
- pull_request targeting main and dev

Checks:

1. Dependency install with uv and dev extras.
2. Version consistency check between pyproject.toml and VERSION.
3. Ruff syntax/runtime checks (E9,F63,F7,F82).
4. Ruff format check for tests and scripts.
5. Pytest run with marker filter not llm.

Rationale:

- Keeps CI fast and reliable without model endpoints.
- Catches syntax/runtime mistakes early.
- Enforces release metadata consistency continuously.

## Release Workflow

Trigger:

- push tag matching vX.Y.Z

Release gates:

1. Quality gate: run the same lint and test checks as the CI workflow (dependency install, version consistency, ruff, pytest).
2. Validate tag format is semantic and stable (vX.Y.Z).
3. Validate tag version equals:
   - project.version in pyproject.toml
   - contents of VERSION file

Job dependency chain: ci → validate → build → release.

Build matrix targets:

- macOS Apple Silicon: aarch64-apple-darwin
- macOS Intel: x86_64-apple-darwin
- Windows x64: x86_64-pc-windows-msvc
- Linux x64: x86_64-unknown-linux-gnu

Build process:

1. Install build dependencies with uv.
2. Run scripts/build-sidecar.sh in production mode:
   - Skip Tauri copy stage.
   - Keep deterministic output from dist/klin-worker[.exe].
   - Emit build metadata JSON (size, SHA256, target, tool versions).
3. Rename assets to include version and target triple:
   - klin-worker-<version>-<target-triple>[.exe]
4. Generate SHA256 checksum files per artifact.
5. Upload artifacts and publish to GitHub Release assets.
6. Publish aggregate SHA256SUMS.txt.

## Versioning Policy

Single release version must be consistent across:

- pyproject.toml: project.version
- VERSION file
- Git tag name: vX.Y.Z

Runtime version reporting order in app config:

1. KLIN_APP_VERSION environment variable (if present)
2. VERSION file (packaged or repository)
3. pyproject.toml project.version
4. fallback: 0.0.0

This avoids drift between API-reported version and release artifacts.

## Sidecar Packaging Notes

- Build script: scripts/build-sidecar.sh
- PyInstaller spec: klin-worker.spec
- VERSION file is bundled into the executable package.
- Existing local developer path copy-to-Tauri remains unchanged unless CI mode is enabled.

## Security and Operational Practices

- Least privilege workflow permissions.
- Concurrency guards for CI and release to avoid duplicate runs.
- Release assets are immutable and version-addressable.
- Artifact checksums are always published.
- Code signing/notarization is intentionally out of scope for this phase.

## Operator Runbook

1. Bump version in pyproject.toml and VERSION.
2. Ensure CI passes on dev/main.
3. Tag release from the commit to ship:

   git tag vX.Y.Z
   git push origin vX.Y.Z

4. Verify release job succeeds.
5. Download artifacts from GitHub Release and verify checksums.

## Rollback

If a bad release is produced:

1. Do not overwrite assets in-place.
2. Create a new patch tag with fixes (for example vX.Y.(Z+1)).
3. Publish corrected release via standard tag pipeline.

## Future Enhancements

- Add optional scheduled integration job with live llama-server.
- Add code-signing and notarization stages behind protected secrets.
- Add SBOM and provenance attestations for supply-chain hardening.
