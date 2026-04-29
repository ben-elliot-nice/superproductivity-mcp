# Gitflow & CI Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add version-gate, build-check, and pre-release publish workflows to main, then wire up branch protection for main and dev.

**Architecture:** Four workflow files pushed directly to main (so gates are active when PR #5 is reviewed), followed by dev branch creation and branch protection via `gh api`. No test files — workflow runs themselves serve as integration tests.

**Tech Stack:** GitHub Actions, `gh` CLI, Python `packaging` library, uv, bash

---

## File Map

| Action | Path |
|--------|------|
| Create | `.github/workflows/version-gate.yml` |
| Create | `.github/workflows/build.yml` |
| Create | `.github/workflows/publish.yml` |
| Create | `.github/workflows/publish-dev.yml` |

> **Note on PR #5 (feat/packaging):** Once these workflows land on main, PR #5 will trigger `version-gate` and `build` checks. It will fail `version-gate` because its versions are still `1.2.1` while main is now `1.2.2`. PR #5 must be rebased and version-bumped to `1.2.2` before it can merge.

> **Note on publish-dev.yml:** Requires `package = true` in `pyproject.toml` (added by PR #5) and the `dev` branch to exist. Harmless to land on main early — it simply won't fire until both preconditions exist.

---

## Task 1: Version gate workflow

**Files:**
- Create: `.github/workflows/version-gate.yml`

- [ ] **Step 1: Create `.github/workflows/version-gate.yml`**

```yaml
name: Version gate

on:
  pull_request:
    branches: [main]

jobs:
  version-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Check version consistency and bump
        run: |
          pip install packaging --quiet
          python3 << 'PYEOF'
          import json, re, sys, subprocess
          from pathlib import Path
          from packaging.version import Version

          versions = {}

          # pyproject.toml (required)
          text = Path("pyproject.toml").read_text()
          m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
          if not m:
              print("ERROR: version not found in pyproject.toml")
              sys.exit(1)
          versions["pyproject.toml"] = m.group(1)

          # src/superproductivity_mcp/__init__.py (present after PR #5 merges)
          init_path = Path("src/superproductivity_mcp/__init__.py")
          if init_path.exists():
              m = re.search(r'^__version__\s*=\s*"([^"]+)"', init_path.read_text(), re.MULTILINE)
              if m:
                  versions["src/superproductivity_mcp/__init__.py"] = m.group(1)

          # src/superproductivity_mcp/server.py (present after PR #5 merges)
          server_path = Path("src/superproductivity_mcp/server.py")
          if server_path.exists():
              m = re.search(r'server_version="([^"]+)"', server_path.read_text())
              if m:
                  versions["src/superproductivity_mcp/server.py"] = m.group(1)

          # plugin/manifest.json (required)
          manifest = json.loads(Path("plugin/manifest.json").read_text())
          versions["plugin/manifest.json"] = manifest["version"]

          print("Versions found:")
          for f, v in sorted(versions.items()):
              print(f"  {f}: {v}")

          # All found versions must match
          unique = set(versions.values())
          if len(unique) > 1:
              print("\nERROR: Version mismatch across files!")
              for f, v in sorted(versions.items()):
                  print(f"  {f}: {v}")
              sys.exit(1)

          pr_version = list(unique)[0]
          print(f"\nAll files consistent: v{pr_version}")

          # Must be strictly greater than current main
          result = subprocess.run(
              ["git", "show", "origin/main:pyproject.toml"],
              capture_output=True, text=True
          )
          m = re.search(r'^version\s*=\s*"([^"]+)"', result.stdout, re.MULTILINE)
          if not m:
              print("WARNING: could not read version from main — skipping bump check")
              sys.exit(0)

          main_version = m.group(1)
          print(f"main: v{main_version}")

          if Version(pr_version) <= Version(main_version):
              print(f"\nERROR: v{pr_version} must be strictly greater than main (v{main_version}).")
              print("Bump the version in pyproject.toml, __init__.py, server.py, and plugin/manifest.json.")
              sys.exit(1)

          print(f"\nVersion bump confirmed: {main_version} -> {pr_version}")
          PYEOF
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/version-gate.yml
git commit -m "ci: add version gate workflow for PRs to main"
```

---

## Task 2: Build checks workflow

**Files:**
- Create: `.github/workflows/build.yml`

- [ ] **Step 1: Create `.github/workflows/build.yml`**

```yaml
name: Build checks

on:
  pull_request:
    branches: [main]

jobs:
  build-plugin:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Build plugin zip
        run: bash build-plugin.sh

      - name: Verify zip exists
        run: |
          VERSION=$(python3 -c "import json; print(json.load(open('plugin/manifest.json'))['version'])")
          ZIP="plugin/plugin-v${VERSION}.zip"
          test -f "$ZIP" && echo "OK: $ZIP" || (echo "ERROR: $ZIP not found" && exit 1)

  build-mcp:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - name: Build MCP wheel
        run: uv build

      - name: Verify artifacts
        run: |
          ls dist/*.whl dist/*.tar.gz \
            && echo "OK: MCP package built" \
            || (echo "ERROR: dist artifacts missing" && exit 1)
```

> **Note:** `build-mcp` will fail on branches that still have `package = false` in `pyproject.toml`. After PR #5 merges (setting `package = true`), this passes for all future PRs from dev.

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/build.yml
git commit -m "ci: add plugin and MCP build check workflows for PRs to main"
```

---

## Task 3: Stable publish workflow (with pre-release guard)

**Files:**
- Create: `.github/workflows/publish.yml`

> This is the authoritative version of `publish.yml` for main. PR #5 also adds this file — when PR #5 merges, accept the main version to keep the pre-release guard.

- [ ] **Step 1: Create `.github/workflows/publish.yml`**

```yaml
name: Publish to PyPI

on:
  push:
    tags:
      - "v*"

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Skip pre-release tags
        id: check
        run: |
          pip install packaging --quiet
          python3 << 'PYEOF'
          import os, sys
          from packaging.version import Version
          tag = os.environ["GITHUB_REF_NAME"].lstrip("v")
          v = Version(tag)
          is_pre = "true" if v.is_prerelease else "false"
          print(f"Tag: {tag}  pre-release: {is_pre}")
          with open(os.environ["GITHUB_OUTPUT"], "a") as f:
              f.write(f"skip={is_pre}\n")
          PYEOF

      - uses: astral-sh/setup-uv@v5
        if: steps.check.outputs.skip == 'false'
        with:
          enable-cache: true

      - name: Build package
        if: steps.check.outputs.skip == 'false'
        run: uv build

      - name: Publish to PyPI
        if: steps.check.outputs.skip == 'false'
        run: uv publish
        env:
          UV_PUBLISH_TOKEN: ${{ secrets.UV_PUBLISH_TOKEN }}
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/publish.yml
git commit -m "ci: add stable PyPI publish workflow with pre-release guard"
```

---

## Task 4: Dev pre-release publish workflow

**Files:**
- Create: `.github/workflows/publish-dev.yml`

- [ ] **Step 1: Create `.github/workflows/publish-dev.yml`**

```yaml
name: Publish dev release

on:
  push:
    branches:
      - dev
    tags:
      - "v*.dev*"
      - "v*a*"
      - "v*b*"
      - "v*rc*"

jobs:
  publish-dev:
    runs-on: ubuntu-latest
    # Run if triggered by a pre-release tag OR a dev push with [publish] in commit message
    if: startsWith(github.ref, 'refs/tags/') || contains(github.event.head_commit.message, '[publish]')
    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - name: Compute pre-release version
        id: version
        run: |
          if [[ "$GITHUB_REF" == refs/tags/* ]]; then
            # Use tag directly: v1.2.2.dev1 -> 1.2.2.dev1
            VERSION="${GITHUB_REF_NAME#v}"
            echo "source=tag" >> "$GITHUB_OUTPUT"
          else
            # Append .devN to current pyproject.toml version
            CURRENT=$(python3 -c "
          import re
          m = re.search(r'version = \"([^\"]+)\"', open('pyproject.toml').read())
          print(m.group(1))
          ")
            VERSION="${CURRENT}.dev${{ github.run_number }}"
            echo "source=keyword" >> "$GITHUB_OUTPUT"
          fi
          echo "version=${VERSION}" >> "$GITHUB_OUTPUT"
          echo "Pre-release version: ${VERSION}"

      - name: Patch pyproject.toml version
        run: |
          sed -i "s/^version = \".*\"/version = \"${{ steps.version.outputs.version }}\"/" pyproject.toml
          grep "^version" pyproject.toml

      - name: Build package
        run: uv build

      - name: Publish to PyPI
        run: uv publish
        env:
          UV_PUBLISH_TOKEN: ${{ secrets.UV_PUBLISH_TOKEN }}
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/publish-dev.yml
git commit -m "ci: add dev pre-release publish workflow"
```

- [ ] **Step 3: Push all workflow commits to main**

```bash
git push origin main
```

Expected: 4 new commits pushed. GitHub Actions will activate the new workflows immediately.

---

## Task 5: Create dev branch

> Do this after PR #5 merges to main, so dev branches from the complete codebase.

- [ ] **Step 1: Create dev from main and push**

```bash
git checkout main && git pull
git checkout -b dev
git push -u origin dev
git checkout main
```

Expected: `dev` branch visible at `github.com/ben-elliot-nice/superproductivity-mcp/tree/dev`.

---

## Task 6: Apply branch protection

> Do this after Task 5 (dev branch must exist) and after the workflow files are on main (status check names must be registered).

- [ ] **Step 1: Protect main — require PR + all three status checks**

```bash
gh api repos/ben-elliot-nice/superproductivity-mcp/branches/main/protection \
  --method PUT \
  --field enforce_admins=false \
  --field 'required_status_checks[strict]=false' \
  --field 'required_status_checks[contexts][]=version-gate' \
  --field 'required_status_checks[contexts][]=build-plugin' \
  --field 'required_status_checks[contexts][]=build-mcp' \
  --field 'required_pull_request_reviews[required_approving_review_count]=0' \
  --field 'required_pull_request_reviews[dismiss_stale_reviews]=false' \
  --field restrictions=null \
  --field allow_force_pushes=false \
  --field allow_deletions=false \
  --silent && echo "main: protected"
```

- [ ] **Step 2: Protect dev — no force push or deletion, direct push allowed**

```bash
gh api repos/ben-elliot-nice/superproductivity-mcp/branches/dev/protection \
  --method PUT \
  --field enforce_admins=false \
  --field required_status_checks=null \
  --field required_pull_request_reviews=null \
  --field restrictions=null \
  --field allow_force_pushes=false \
  --field allow_deletions=false \
  --silent && echo "dev: protected"
```

- [ ] **Step 3: Verify protection is active**

```bash
gh api repos/ben-elliot-nice/superproductivity-mcp/branches/main --jq '.protection.enabled'
gh api repos/ben-elliot-nice/superproductivity-mcp/branches/dev --jq '.protection.enabled'
```

Expected: both print `true`.

---

## Self-Review

**Spec coverage:**
- ✅ `version-gate.yml` — checks all 4 locations, consistent + bumped (Task 1)
- ✅ `build.yml` — plugin + MCP parallel build gates (Task 2)
- ✅ `publish.yml` — stable PyPI on `v*` tag, pre-release guard added (Task 3)
- ✅ `publish-dev.yml` — keyword or tag triggers prerelease publish (Task 4)
- ✅ dev branch creation (Task 5)
- ✅ branch protection for main + dev via `gh api` (Task 6)
- ✅ status check names in Task 6 match job IDs in Tasks 1+2: `version-gate`, `build-plugin`, `build-mcp`

**Placeholders:** None.

**Type consistency:** Job IDs in YAML match `contexts[]` values in branch protection commands exactly.

**Post-merge checklist (not in this plan, but required before first stable release):**
- Update PR #5 to version `1.2.2` so it passes `version-gate`
- Add `UV_PUBLISH_TOKEN` secret in repo Settings → Secrets → Actions
