#!/usr/bin/env python3
"""
deploy.py -- publish generated seller-report pages to GitHub Pages.

Copies rendered report pages (produced by bin/generate.py) into the
`reports/` prefix at the root of the `torabic/mcg-listings` repo -- the same
convention that repo already uses for `presentations/<slug>/` and
`listing-presentations/<slug>/` (GitHub Pages serves the repo root on the
`main` branch; there is no `docs/` dir and no CNAME/custom domain, so the
site is served at https://torabic.github.io/mcg-listings/). It then commits
and pushes only the `reports/` prefix -- existing marketing pages elsewhere
in the repo are never touched.

Usage:
    python3 bin/deploy.py --period-id 2026-W29 \
        [--source-dir docs/reports] [--repo-dir /path/to/mcg-listings] \
        [--repo-url https://github.com/torabic/mcg-listings.git] \
        [--token-file /path/to/token] [--dry-run]

Token resolution (never printed, never committed):
    1. --token-file, if given.
    2. $GITHUB_TOKEN environment variable.
    A file path may contain a label/whitespace around the token; only the
    token-shaped substring is extracted.

Idempotent: re-running with unchanged content is a no-op commit-wise (git
add + diff-check before committing); re-running against an existing
repo-dir clone pulls first (--ff-only) instead of re-cloning.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent  # seller-reports/

DEFAULT_REPO_URL = "https://github.com/torabic/mcg-listings.git"
DEFAULT_BASE_URL = "https://torabic.github.io/mcg-listings"
TOKEN_RE = re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}")


# ---------------------------------------------------------------------------
# Token handling -- never print, never log, never commit.
# ---------------------------------------------------------------------------
def resolve_token(token_file: str | None) -> str | None:
    raw = None
    if token_file:
        p = Path(token_file)
        if not p.exists():
            print(f"ERROR: --token-file {p} does not exist", file=sys.stderr)
            return None
        raw = p.read_text()
    elif os.environ.get("GITHUB_TOKEN"):
        raw = os.environ["GITHUB_TOKEN"]

    if not raw:
        return None
    raw = raw.strip()
    m = TOKEN_RE.search(raw)
    return m.group(0) if m else raw


def make_askpass_script(token: str) -> str:
    """Write a tiny helper GIT_ASKPASS script that emits the token on
    request, so it never appears as a CLI argument, in the process list, or
    in any printed output. Returns the script path (caller cleans it up)."""
    fd, path = tempfile.mkstemp(prefix="deploy-askpass-", suffix=".sh")
    with os.fdopen(fd, "w") as f:
        f.write("#!/bin/sh\necho \"$GIT_DEPLOY_TOKEN\"\n")
    os.chmod(path, 0o700)
    return path


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------
def run(cmd, cwd=None, env=None, check=True):
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({' '.join(cmd)}):\n{result.stdout}\n{result.stderr}"
        )
    return result


def with_username(repo_url: str) -> str:
    """Insert the (non-secret) x-access-token username into an https:// git
    URL so GIT_ASKPASS is only ever asked for the password half."""
    if repo_url.startswith("https://") and "@" not in repo_url:
        return repo_url.replace("https://", "https://x-access-token@", 1)
    return repo_url


def ensure_repo(repo_dir: Path, repo_url: str, git_env: dict) -> None:
    auth_url = with_username(repo_url)
    if (repo_dir / ".git").exists():
        run(["git", "fetch", auth_url, "main"], cwd=repo_dir, env=git_env)
        run(["git", "checkout", "main"], cwd=repo_dir, env=git_env)
        # Fast-forward only. This never discards local commits -- if
        # repo-dir has its own unpushed work (e.g. a manual integration
        # commit made outside this script) that has diverged from
        # origin/main, fail loudly here rather than silently reset --hard
        # over it.
        result = run(["git", "merge", "--ff-only", "FETCH_HEAD"], cwd=repo_dir, env=git_env, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"repo-dir {repo_dir} has diverged from origin/main and can't "
                "fast-forward -- resolve manually (rebase/push/reset as "
                "appropriate) before re-running deploy.py. Refusing to "
                f"discard local history.\n{result.stdout}\n{result.stderr}"
            )
    else:
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--depth", "50", auth_url, str(repo_dir)], env=git_env)


def copy_reports(source_dir: Path, repo_dir: Path) -> list[str]:
    """Copy every slug_token/period_id (and slug_token/latest) directory
    from source_dir into repo_dir/reports/<slug_token>/..., merging with
    whatever is already there. Returns the list of slug_token dirs copied."""
    dest_root = repo_dir / "reports"
    dest_root.mkdir(parents=True, exist_ok=True)
    copied = []
    if not source_dir.exists():
        raise RuntimeError(f"source dir {source_dir} does not exist -- run bin/generate.py first")
    for slug_token_dir in sorted(source_dir.iterdir()):
        if not slug_token_dir.is_dir():
            continue
        dest = dest_root / slug_token_dir.name
        shutil.copytree(slug_token_dir, dest, dirs_exist_ok=True)
        copied.append(slug_token_dir.name)
    return copied


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Deploy rendered seller reports to GitHub Pages.")
    ap.add_argument("--period-id", required=True, help="e.g. 2026-W29, 2026-07, 2026-Q3 -- used in the commit message")
    ap.add_argument("--source-dir", default=str(REPO_ROOT / "docs" / "reports"),
                     help="where bin/generate.py wrote rendered pages (its --outdir)")
    ap.add_argument("--repo-dir", default=str(REPO_ROOT.parent / "mcg-listings-deploy"),
                     help="local clone of the Pages repo (cloned if it doesn't exist yet)")
    ap.add_argument("--repo-url", default=DEFAULT_REPO_URL)
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL,
                     help="root URL Pages serves the repo at, used to print published URLs")
    ap.add_argument("--token-file", default=None, help="file containing the GitHub token (falls back to $GITHUB_TOKEN)")
    ap.add_argument("--dry-run", action="store_true", help="copy + show what would be committed, but do not commit/push")
    args = ap.parse_args()

    source_dir = Path(args.source_dir)
    repo_dir = Path(args.repo_dir)

    token = resolve_token(args.token_file)
    if not token and not args.dry_run:
        print("ERROR: no GitHub token found (--token-file or $GITHUB_TOKEN). "
              "Refusing to push. Pass --dry-run to test the copy step without pushing.", file=sys.stderr)
        return 1

    git_env = os.environ.copy()
    git_env["GIT_TERMINAL_PROMPT"] = "0"
    askpass_path = None
    if token:
        askpass_path = make_askpass_script(token)
        git_env["GIT_ASKPASS"] = askpass_path
        git_env["GIT_DEPLOY_TOKEN"] = token
        git_env["GIT_USERNAME"] = "x-access-token"

    try:
        ensure_repo(repo_dir, args.repo_url, git_env)

        copied = copy_reports(source_dir, repo_dir)
        if not copied:
            print("[deploy] nothing to copy from source dir -- aborting", file=sys.stderr)
            return 1
        print(f"[deploy] copied {len(copied)} report tree(s): {', '.join(copied)}")

        run(["git", "add", "reports"], cwd=repo_dir, env=git_env)
        diff = run(["git", "diff", "--cached", "--name-only"], cwd=repo_dir, env=git_env)
        changed_files = [l for l in diff.stdout.splitlines() if l.strip()]

        if not changed_files:
            print(f"[deploy] no changes under reports/ for period {args.period_id} -- already up to date, nothing to commit/push.")
        else:
            print(f"[deploy] {len(changed_files)} file(s) staged for commit.")
            if args.dry_run:
                print("[deploy] --dry-run: skipping commit + push.")
            else:
                run(["git", "config", "user.email", "seller-reports@mcg-bot.local"], cwd=repo_dir, env=git_env)
                run(["git", "config", "user.name", "MCG Seller Reports Bot"], cwd=repo_dir, env=git_env)
                run(["git", "commit", "-m", f"seller-reports: {args.period_id} report run"], cwd=repo_dir, env=git_env)

                # Push over HTTPS using the askpass helper -- the token itself
                # is never embedded in the remote URL, a CLI arg, or printed
                # (only the non-secret "x-access-token" username is).
                push_url = with_username(args.repo_url)
                run(["git", "push", push_url, "HEAD:main"], cwd=repo_dir, env=git_env)
                print("[deploy] pushed to origin/main.")

        print("\n[deploy] published report URLs:")
        for slug_token in copied:
            period_dir = repo_dir / "reports" / slug_token / args.period_id
            if period_dir.exists():
                print(f"  {args.base_url}/reports/{slug_token}/{args.period_id}/")
            latest_dir = repo_dir / "reports" / slug_token / "latest"
            if latest_dir.exists():
                print(f"  {args.base_url}/reports/{slug_token}/latest/")
        return 0
    finally:
        if askpass_path and os.path.exists(askpass_path):
            os.remove(askpass_path)


if __name__ == "__main__":
    sys.exit(main())
