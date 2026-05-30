---
description: Cut a new lto-backup release — bump version, tag, push, and create a GitHub release.
---

Run `scripts/release.py` to cut a new release of lto-backup.

## Steps

1. **Choose a version bump** — ask the user which part to bump (`major`, `minor`, `patch`) or for an explicit `X.Y.Z` version.
2. **Optional: release notes** — ask if the user wants custom notes, or leave blank to let `gh` auto-generate them from merged PRs / commits.
3. **Optional: draft?** — ask if the release should be created as a draft on GitHub (useful for review before publishing).
4. **Dry-run first** — always run with `--dry-run` to show what would happen, then ask the user to confirm before proceeding.
5. **Run the release** — execute the script without `--dry-run`.

## Prerequisites

Verify before starting:
- Working tree is clean (`git status --porcelain` returns nothing).
- Current branch is `main`.
- `gh` CLI is installed and authenticated (`gh auth status`).

## Example commands

```powershell
# Preview a patch bump
python scripts/release.py patch --dry-run

# Patch bump with auto-generated GitHub release notes
python scripts/release.py patch

# Minor bump with custom notes
python scripts/release.py minor --notes "Adds restore support for zero-byte files."

# Explicit version, created as draft
python scripts/release.py 1.0.0 --draft
```

## Notes

- The script edits `pyproject.toml`, commits, tags, pushes, and calls `gh release create`.
- `--generate-notes` is always passed to `gh`; if `--notes TEXT` is also given, GitHub appends both.
- If anything fails mid-way, fix the issue manually: check which steps completed and resume from where it stopped.
