# GitHub Branch Protection Configuration

> **Purpose:** Document the exact GitHub branch protection settings for reproducibility.
> **Last Updated:** January 2026

---

## Required Status Checks

The CI workflow (`.github/workflows/test.yml`) emits this status check:

| Check Name | Job ID | What It Does |
|------------|--------|--------------|
| `test` | `test` | Runs all validation: config, lint, types, architecture, contracts, tests |

**To find check names:**

1. Go to GitHub → Actions → Any completed workflow run
2. Look at the job names in the left sidebar
3. These are your status check IDs

---

## Branch: `main`

**Path:** Settings → Branches → Add rule → Branch name pattern: `main`

### Protect matching branches

- [x] **Require a pull request before merging**
  - [x] Require approvals: `1`
  - [ ] Dismiss stale pull request approvals when new commits are pushed
  - [ ] Require review from Code Owners
  - [x] Require approval of the most recent reviewable push

- [x] **Require status checks to pass before merging**
  - [x] Require branches to be up to date before merging
  - Status checks that are required:
    - `test` ← **Add this** (type "test" and select it)

- [ ] Require conversation resolution before merging
- [ ] Require signed commits
- [ ] Require linear history

### Rules applied to everyone including administrators

- [ ] Allow force pushes → **OFF**
- [ ] Allow deletions → **OFF**

---

## Branch: `develop`

**Path:** Settings → Branches → Add rule → Branch name pattern: `develop`

### Protect matching branches

- [x] **Require a pull request before merging**
  - [x] Require approvals: `0` (faster iteration during development)

- [x] **Require status checks to pass before merging**
  - [x] Require branches to be up to date before merging
  - Status checks that are required:
    - `test`

- [ ] Allow force pushes → **OFF**
- [ ] Allow deletions → **OFF**

---

## Setup Steps

### Step 1: Navigate to Branch Protection

```
GitHub Repo → Settings (gear icon) → Branches (left sidebar) → Add branch protection rule
```

### Step 2: Configure `main` Branch

1. **Branch name pattern:** `main`
2. Check: **"Require a pull request before merging"**
3. Set **Required approvals** to `1`
4. Check: **"Require status checks to pass before merging"**
5. In the search box, type `test` and select it
6. Check: **"Require branches to be up to date before merging"**
7. Click **"Save changes"**

### Step 3: Configure `develop` Branch

1. **Branch name pattern:** `develop`
2. Check: **"Require a pull request before merging"**
3. Set **Required approvals** to `0` (or `1` if you want stricter)
4. Check: **"Require status checks to pass before merging"**
5. Search for `test` and select it
6. Click **"Save changes"**

---

## Verification

After setup, verify protection works:

```bash
# This should FAIL (direct push to main)
git checkout main
echo "test" >> test.txt
git add test.txt
git commit -m "test direct push"
git push origin main
# Expected: rejected - requires pull request

# This should WORK (PR workflow)
git checkout -b test/verify-protection
echo "test" >> test.txt
git add test.txt
git commit -m "test PR workflow"
git push origin test/verify-protection
gh pr create --base main --title "Test PR" --body "Testing branch protection"
# Expected: PR created, CI runs, requires approval before merge

# Cleanup
git checkout main
git branch -D test/verify-protection
gh pr close --delete-branch
```

---

## CI Workflow Reference

From `.github/workflows/test.yml`:

```yaml
name: Tests

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main, develop]

jobs:
  test:  # ← This is the status check name
    runs-on: ubuntu-latest
    steps:
      # Phase 1: Config validation
      - name: Validate config against specs
        run: python scripts/validate_config.py

      # Phase 2: Code quality
      - name: Run linting (black)
        run: black --check ...
      - name: Run type checking (mypy)
        run: mypy ...

      # Phase 3: Architecture & Contract tests
      - name: Run architecture boundary tests
        run: pytest tests/test_architecture_boundaries.py -v
      - name: Run contract tests (TargetWeight)
        run: pytest tests/test_target_weight_contract.py -v
      - name: Run smoke integration tests
        run: pytest tests/test_smoke_integration.py -v

      # Phase 4: Unit tests
      - name: Run all unit tests
        run: pytest tests/ -v

      # Phase 5: Coverage
      - name: Run tests with coverage
        run: pytest tests/ --cov=engines ...

      # Phase 6: Documentation
      - name: Documentation Parity Check
        run: python scripts/check_spec_parity.py
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Status check not appearing in dropdown | Run the workflow at least once on a PR first |
| Check name is different | Look at Actions → Job name in left sidebar |
| CI passes but merge blocked | Check if approval is required |
| Admin bypassing protection | Admins can bypass by default; uncheck "Include administrators" if needed |
| Status check stuck "pending" | Check Actions tab for failed/hung workflow |

---

## Why These Settings?

| Setting | Purpose |
|---------|---------|
| Require PR | No direct pushes; all changes reviewed |
| Require `test` check | CI must pass; catches bugs before merge |
| Require up-to-date | No merge conflicts; linear history |
| 1 approval for `main` | Human review before production code |
| 0 approvals for `develop` | Faster iteration during development |
| No force push | Preserve history; prevent accidents |
| No deletions | Protect branch from accidental removal |
