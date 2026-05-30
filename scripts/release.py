#!/usr/bin/env python3
"""Bump version, tag, and create a GitHub release.

Usage:
    python scripts/release.py patch          # 0.1.1 -> 0.1.2
    python scripts/release.py minor          # 0.1.1 -> 0.2.0
    python scripts/release.py major          # 0.1.1 -> 1.0.0
    python scripts/release.py 1.2.3          # explicit version

Requires:
    - git (clean working tree on main)
    - gh CLI (authenticated)
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"
VERSION_RE = re.compile(r'^(version\s*=\s*")(\d+\.\d+\.\d+)(")', re.MULTILINE)


def read_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    m = VERSION_RE.search(text)
    if not m:
        sys.exit("Could not find version = \"...\" in pyproject.toml")
    return m.group(2)


def write_version(new_version: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    new_text = VERSION_RE.sub(rf'\g<1>{new_version}\g<3>', text)
    PYPROJECT.write_text(new_text, encoding="utf-8")


def bump(current: str, part: str) -> str:
    major, minor, patch = (int(x) for x in current.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    # explicit version
    if re.fullmatch(r"\d+\.\d+\.\d+", part):
        return part
    sys.exit(f"Invalid version argument: {part!r}")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def git_is_clean() -> bool:
    result = run(["git", "status", "--porcelain"])
    return result.stdout.strip() == ""


def git_current_branch() -> str:
    result = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "bump",
        metavar="PART_OR_VERSION",
        help="'major', 'minor', 'patch', or an explicit 'X.Y.Z' version.",
    )
    parser.add_argument(
        "--notes",
        metavar="TEXT",
        default="",
        help="Release notes to include in the GitHub release body.",
    )
    parser.add_argument(
        "--draft",
        action="store_true",
        help="Create the GitHub release as a draft.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without making any changes.",
    )
    args = parser.parse_args()

    current = read_version()
    new_version = bump(current, args.bump)
    tag = f"v{new_version}"

    print(f"\nlto-backup release: {current} → {new_version} ({tag})\n")

    branch = git_current_branch()
    if branch != "main":
        sys.exit(f"Must be on main branch (currently on '{branch}').")

    if not git_is_clean():
        sys.exit("Working tree is not clean. Commit or stash changes first.")

    if args.dry_run:
        print("[dry-run] Would:")
        print(f"  1. Write version {new_version} to pyproject.toml")
        print(f"  2. git commit -am 'chore: bump version to {new_version}'")
        print(f"  3. git tag {tag}")
        print(f"  4. git push && git push origin {tag}")
        print(f"  5. gh release create {tag} ...")
        return

    # 1. Bump pyproject.toml
    print(f"[1/5] Writing version {new_version} to pyproject.toml")
    write_version(new_version)

    # 2. Commit
    print(f"[2/5] Committing version bump")
    run(["git", "commit", "-am", f"chore: bump version to {new_version}"])

    # 3. Tag
    print(f"[3/5] Creating tag {tag}")
    run(["git", "tag", tag])

    # 4. Push
    print("[4/5] Pushing branch and tag")
    run(["git", "push"])
    run(["git", "push", "origin", tag])

    # 5. GitHub release
    print("[5/5] Creating GitHub release")
    gh_cmd = [
        "gh", "release", "create", tag,
        "--title", f"lto-backup {new_version}",
        "--generate-notes",
    ]
    if args.notes:
        gh_cmd += ["--notes", args.notes]
    if args.draft:
        gh_cmd.append("--draft")
    result = run(gh_cmd)
    release_url = result.stdout.strip()

    print(f"\nRelease created: {release_url}")


if __name__ == "__main__":
    main()
