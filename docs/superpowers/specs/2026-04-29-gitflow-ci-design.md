# Gitflow & CI Pipeline Design

**Date:** 2026-04-29  
**Status:** Approved

## Goal

Establish a proper gitflow with branch protection, version consistency gates, and PyPI prerelease publishing for the `dev` branch — so `main` only ever contains releasable, fully-versioned code.

## Out of Scope

- Changelog automation
- Semantic versioning enforcement (manual bumps only)
- Multiple release tracks (single semver line only)

---

## Branch Structure

```
main  ←── PRs only (version gate + build checks required)
 ↑
dev   ←── direct push allowed, protected against force push/deletion
```

`dev` is the integration branch. Developers push directly to `dev`, optionally publish pre-releases to test, then open a PR from `dev` → `main` when ready for a stable release.

---

## Workflow Files

Five files in `.github/workflows/`:

| File | Trigger | Purpose |
|------|---------|---------|
| `version-gate.yml` | PR to `main` | All 4 version locations match + bumped from main |
| `build.yml` | PR to `main` | Plugin zip + MCP wheel build checks (parallel jobs) |
| `release.yml` | push `main` | Plugin zip → GitHub Release (existing, unchanged) |
| `publish.yml` | push `v*` tag | Stable PyPI publish (token-based, UV_PUBLISH_TOKEN) |
| `publish-dev.yml` | push to `dev` w/ `[publish]` in commit msg OR pre-release tag | Prerelease PyPI publish |

---

## Version Gate (`version-gate.yml`)

Runs on every PR targeting `main`. Single job named `version-gate`.

Checks:
1. All four version strings are identical:
   - `pyproject.toml` → `version = "X.Y.Z"`
   - `src/superproductivity_mcp/__init__.py` → `__version__ = "X.Y.Z"`
   - `src/superproductivity_mcp/server.py` → `server_version="X.Y.Z"` (in `InitializationOptions`)
   - `plugin/manifest.json` → `"version": "X.Y.Z"`
2. The version is strictly greater than the current version on `main` (compares using Python's `packaging.version`).

Fails with a clear message identifying which file mismatches or if version is not bumped.

Uses Python's `packaging` library for version comparison (`pip install packaging` in workflow — available on all GitHub-hosted runners via pip).

---

## Build Gate (`build.yml`)

Runs on every PR targeting `main`. Two parallel jobs:

- `build-plugin`: runs `bash build-plugin.sh`, verifies the output zip exists
- `build-mcp`: installs uv, runs `uv build`, verifies `.whl` and `.tar.gz` exist in `dist/`

Both must pass. Neither uploads artifacts (gate only).

---

## Stable Release (`publish.yml`)

Existing workflow from PR #5. Triggers on push of `v*` tags (e.g. `v1.2.1`). Builds and publishes to PyPI using `UV_PUBLISH_TOKEN`. No changes needed.

---

## Dev Prerelease (`publish-dev.yml`)

Triggers on push to `dev` branch in two cases:

**Case 1 — commit keyword:** Last commit message on `dev` contains `[publish]`
- Computes version: `{pyproject_version}.dev{GITHUB_RUN_NUMBER}` (e.g. `1.2.2.dev42`)

**Case 2 — pre-release tag:** Tag matching `v*.dev*`, `v*a[0-9]*`, `v*b[0-9]*`, or `v*rc[0-9]*`
- Computes version: tag stripped of leading `v` (e.g. `v1.2.2.dev1` → `1.2.2.dev1`)

In both cases:
1. Patch `pyproject.toml` version in-place (not committed — ephemeral build only)
2. `uv build`
3. `uv publish` with `UV_PUBLISH_TOKEN`

**End-user impact:** `uvx superproductivity-mcp` always installs the latest stable version. Pre-releases require `uvx --pre superproductivity-mcp` or explicit pinning. PyPI enforces this automatically for any version containing `.dev`, `a`, `b`, or `rc`.

---

## Branch Protection

Applied via `gh api` after workflows are merged to `main` (status checks must exist before they can be required).

**main:**
- No direct push
- Requires PR
- Required status checks: `version-gate`, `build-plugin`, `build-mcp`
- No force push
- No deletion

**dev:**
- Direct push allowed
- No PR required
- No force push
- No deletion
- Created from `main` if it doesn't exist

---

## Setup Order

1. Merge PR #5 (packaging restructure) to `main`
2. Push workflow files directly to `main` (so checks exist before branch protection)
3. Create `dev` branch from `main`
4. Apply branch protection to both branches
5. Add `UV_PUBLISH_TOKEN` secret in GitHub repo settings before first release

---

## Status Check Names

These must match job IDs in workflow files exactly — they're what branch protection references:

| Check name | File | Job ID |
|------------|------|--------|
| `version-gate` | `version-gate.yml` | `version-gate` |
| `build-plugin` | `build.yml` | `build-plugin` |
| `build-mcp` | `build.yml` | `build-mcp` |
