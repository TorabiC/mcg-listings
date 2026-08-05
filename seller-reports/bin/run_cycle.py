#!/usr/bin/env python3
"""
run_cycle.py -- one-command MCG seller-report cycle runner, built for a
scheduled session to fire hands-off. Chains the existing
bin/collect.py -> bin/generate.py -> bin/deploy.py pipeline for the
current (or given) period, scoped to Featured Listings by default (see
bin/featured.py / collect.py & generate.py's --include-unfeatured), and
tolerates bin/deploy.py's push failing exactly the way this session has
been doing it by hand every run: fall back to a `git format-patch` of
the deploy clone's unpushed commit(s), written to
/home/claude/work/cycle-<date>.patch, and print PATCH-NEEDED so a human
(or a later run once push access is restored) can apply it.

This script deliberately does NOT re-implement collect.py/generate.py's
per-listing loop, source adapters, or template rendering -- it drives
those scripts as subprocesses (their CLI is the stable, tested contract)
and only imports bin/featured.py directly, to resolve the featured-
listing set once up front for the run summary. deploy.py is likewise
called as-is; run_cycle.py only adds the patch-file fallback around it.

Usage:
    python3 bin/run_cycle.py [--period-id 2026-W32] [--monthly-too]
        [--skip-harvest] [--pdf] [--include-unfeatured] [--dry-run]

--period-id     Explicit weekly period id (e.g. 2026-W32). Defaults to
                 the ISO week containing today.
--monthly-too   Additionally run the current calendar month (YYYY-MM) as
                 a monthly rollup, after the weekly run.
--skip-harvest  Skip bin/collect.py (the "harvest" step) and render off
                 whatever data/<slug>/<period_id>/metrics.json already
                 exists on disk.
--pdf           Passed through to bin/generate.py (default: --no-pdf,
                 matching this script's brief).
--include-unfeatured  Passed through to collect.py/generate.py: process
                 every active registry listing, not just Featured
                 Listings-page listings.
--dry-run       Passed through to bin/deploy.py -- copies + shows what
                 would be committed but never commits/pushes. Useful for
                 proving the chain works without touching the deploy
                 remote.

Always ends with exactly one machine-readable JSON line on stdout (after
all human-readable step output) summarizing the run: listings processed,
per-source freshness counts, and any failures, for the scheduled session
to relay to Cameron.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

sys.path.insert(0, str(SCRIPT_DIR))
from featured import get_featured_slugs  # noqa: E402

PYTHON = sys.executable
PATCH_DIR = Path("/home/claude/work")
DEFAULT_DEPLOY_CLONE = REPO_ROOT.parent / "mcg-listings-deploy"


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------
def current_iso_week() -> str:
    today = date.today()
    iso_year, iso_week, _ = today.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def current_month() -> str:
    return date.today().strftime("%Y-%m")


# ---------------------------------------------------------------------------
# Subprocess helper -- every bin/*.py call goes through here so failures
# are captured, not raised, and every step's stdout/stderr streams to the
# console live (this is meant to be watched by a scheduled session, not
# just read after the fact).
# ---------------------------------------------------------------------------
def run_step(cmd: list[str], label: str) -> dict:
    print(f"[run_cycle] $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    ok = proc.returncode == 0
    if proc.stdout:
        sys.stdout.write(proc.stdout if proc.stdout.endswith("\n") else proc.stdout + "\n")
    if proc.stderr:
        sys.stderr.write(proc.stderr if proc.stderr.endswith("\n") else proc.stderr + "\n")
    if not ok:
        print(f"[run_cycle] {label} FAILED (exit {proc.returncode})", file=sys.stderr)
    return {
        "label": label, "cmd": cmd, "returncode": proc.returncode, "ok": ok,
        "stdout": proc.stdout, "stderr": proc.stderr,
    }


# ---------------------------------------------------------------------------
# Deploy + PATCH-NEEDED fallback
# ---------------------------------------------------------------------------
def deploy_with_patch_fallback(period_id: str, dry_run: bool) -> dict:
    """Call bin/deploy.py; if it fails to push (the normal case in this
    sandboxed session -- no route to actually push to torabic/mcg-listings),
    fall back to writing a `git format-patch` of whatever commit(s)
    deploy.py made locally in its clone but couldn't push, to
    /home/claude/work/cycle-<date>.patch, and print PATCH-NEEDED --
    exactly the manual fallback this session has used on every prior
    report run."""
    cmd = [PYTHON, "bin/deploy.py", "--period-id", period_id]
    if dry_run:
        cmd.append("--dry-run")
    step = run_step(cmd, f"deploy ({period_id})")
    result = {"period_id": period_id, "pushed": step["ok"] and not dry_run,
              "dry_run": dry_run, "patch_file": None, "patch_error": None}

    if step["ok"]:
        return result

    print("[run_cycle] PATCH-NEEDED: bin/deploy.py could not push -- writing a format-patch instead.")
    repo_dir = DEFAULT_DEPLOY_CLONE
    if not repo_dir.exists():
        msg = f"{repo_dir} does not exist -- deploy.py must have failed before cloning; nothing to patch."
        print(f"[run_cycle] PATCH-NEEDED: {msg}", file=sys.stderr)
        result["patch_error"] = msg
        return result

    patch_path = PATCH_DIR / f"cycle-{date.today().isoformat()}.patch"

    # Prefer the upstream-tracking range (everything ahead of the last
    # known remote state); fall back to just the most recent commit if no
    # upstream tracking is configured on this clone.
    fmt = subprocess.run(["git", "format-patch", "@{u}..HEAD", "--stdout"],
                          cwd=repo_dir, capture_output=True, text=True)
    if fmt.returncode != 0 or not fmt.stdout.strip():
        fmt = subprocess.run(["git", "format-patch", "-1", "HEAD", "--stdout"],
                              cwd=repo_dir, capture_output=True, text=True)

    if fmt.returncode == 0 and fmt.stdout.strip():
        patch_path.write_text(fmt.stdout)
        print(f"[run_cycle] PATCH-NEEDED: wrote {patch_path} -- apply by hand once push access is restored.")
        result["patch_file"] = str(patch_path)
    else:
        msg = f"git format-patch produced nothing (exit {fmt.returncode}): {fmt.stderr.strip()}"
        print(f"[run_cycle] PATCH-NEEDED: {msg}", file=sys.stderr)
        result["patch_error"] = msg
    return result


# ---------------------------------------------------------------------------
# Freshness rollup -- read back each processed listing's metrics.json
# (already written by collect.py) rather than re-deriving anything.
# ---------------------------------------------------------------------------
def compute_freshness_counts(period_id: str, slugs: list[str]) -> dict:
    counts: dict[str, dict[str, int]] = {}
    for slug in slugs:
        metrics_path = REPO_ROOT / "data" / slug / period_id / "metrics.json"
        if not metrics_path.exists():
            continue
        try:
            metrics = json.loads(metrics_path.read_text())
        except (ValueError, OSError):
            continue
        for source, entry in (metrics.get("source_freshness") or {}).items():
            status = (entry or {}).get("status", "unknown")
            counts.setdefault(source, {}).setdefault(status, 0)
            counts[source][status] += 1
    return counts


# ---------------------------------------------------------------------------
# Per-period pipeline
# ---------------------------------------------------------------------------
def run_period(period_type: str, period_id: str, featured_slugs: list[str],
               skip_harvest: bool, want_pdf: bool, include_unfeatured: bool,
               dry_run_deploy: bool) -> dict:
    print(f"\n[run_cycle] ==== {period_type} period {period_id} ====")
    period_summary: dict = {
        "period_type": period_type, "period_id": period_id, "steps": [],
        "failures": [],
    }

    if not skip_harvest:
        cmd = [PYTHON, "bin/collect.py", "--period", period_type, "--period-id", period_id]
        if include_unfeatured:
            cmd.append("--include-unfeatured")
        collect_step = run_step(cmd, f"collect ({period_type} {period_id})")
        period_summary["steps"].append(collect_step)
        if not collect_step["ok"]:
            period_summary["failures"].append({"step": "collect", "period_id": period_id,
                                                 "detail": collect_step["stderr"].strip()[-500:]})
    else:
        print(f"[run_cycle] --skip-harvest: not re-collecting for {period_id}, "
              "rendering off whatever metrics.json is already on disk.")

    gen_cmd = [PYTHON, "bin/generate.py", "--period-id", period_id]
    if include_unfeatured:
        gen_cmd.append("--include-unfeatured")
    gen_cmd.append("--pdf" if want_pdf else "--no-pdf")
    gen_step = run_step(gen_cmd, f"generate ({period_type} {period_id})")
    period_summary["steps"].append(gen_step)
    if not gen_step["ok"]:
        period_summary["failures"].append({"step": "generate", "period_id": period_id,
                                             "detail": gen_step["stderr"].strip()[-500:]})

    gen_results = []
    if gen_step["stdout"].strip():
        # generate.py's entire stdout is a single pretty-printed JSON object
        # (indent=2 -- everything else it prints goes to stderr), so parse
        # the whole capture, not just the last line.
        try:
            gen_json = json.loads(gen_step["stdout"])
            gen_results = gen_json.get("results", [])
        except ValueError as exc:
            print(f"[run_cycle] WARNING: could not parse generate.py's JSON output: {exc}", file=sys.stderr)

    processed_slugs = [r["slug"] for r in gen_results if r.get("status") == "OK"]
    for r in gen_results:
        if r.get("status") != "OK":
            period_summary["failures"].append({"step": "generate:listing", "period_id": period_id,
                                                 "slug": r.get("slug"), "detail": r.get("status")})

    period_summary["listings_targeted"] = featured_slugs
    period_summary["listings_processed"] = processed_slugs
    period_summary["source_freshness_counts"] = compute_freshness_counts(period_id, processed_slugs)

    deploy_result = deploy_with_patch_fallback(period_id, dry_run_deploy)
    period_summary["deploy"] = deploy_result
    if not deploy_result["pushed"] and not deploy_result["dry_run"]:
        period_summary["failures"].append({"step": "deploy:push", "period_id": period_id,
                                             "detail": deploy_result.get("patch_error") or
                                                       f"push failed; patch at {deploy_result.get('patch_file')}"})

    return period_summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="One-command MCG seller-report cycle runner.")
    ap.add_argument("--period-id", default=None,
                     help="Weekly period id, e.g. 2026-W32. Defaults to the ISO week containing today.")
    ap.add_argument("--monthly-too", action="store_true",
                     help="Also run the current calendar month as a monthly rollup.")
    ap.add_argument("--skip-harvest", action="store_true",
                     help="Skip bin/collect.py; render off whatever metrics.json already exists.")
    ap.add_argument("--pdf", action="store_true",
                     help="Pass --pdf through to bin/generate.py (default: --no-pdf).")
    ap.add_argument("--include-unfeatured", action="store_true",
                     help="Process every active registry listing, not just Featured Listings.")
    ap.add_argument("--dry-run", action="store_true",
                     help="Pass --dry-run through to bin/deploy.py: copy + show what would be "
                          "committed, but never commit/push.")
    args = ap.parse_args(argv)

    weekly_period_id = args.period_id or current_iso_week()
    run_started_at = datetime.now(timezone.utc).isoformat()

    if args.include_unfeatured:
        featured_slugs = None  # resolved per-tool; summary reports registry-wide
        print("[run_cycle] --include-unfeatured: skipping Featured Listings scoping for this run.")
    else:
        featured = get_featured_slugs()
        featured_slugs = featured.slugs
        print(f"[run_cycle] featured listings this run ({featured.source}): "
              f"{', '.join(featured_slugs) if featured_slugs else '(none)'}")
        if featured.skipped_cards:
            for card_slug, reason in featured.skipped_cards:
                print(f"[run_cycle]   skipped card {card_slug!r}: {reason}")
        if featured.source == "fallback":
            print(f"[run_cycle] WARNING: {featured.warning}", file=sys.stderr)

    periods = []
    weekly_summary = run_period("weekly", weekly_period_id, featured_slugs or [],
                                 args.skip_harvest, args.pdf, args.include_unfeatured, args.dry_run)
    periods.append(weekly_summary)

    if args.monthly_too:
        monthly_summary = run_period("monthly", current_month(), featured_slugs or [],
                                      args.skip_harvest, args.pdf, args.include_unfeatured, args.dry_run)
        periods.append(monthly_summary)

    all_failures = [f for p in periods for f in p["failures"]]

    summary = {
        "run_started_at": run_started_at,
        "run_finished_at": datetime.now(timezone.utc).isoformat(),
        "featured_scoping": "include_unfeatured" if args.include_unfeatured else "featured_only",
        "featured_listing_count": len(featured_slugs) if featured_slugs is not None else None,
        "periods": [
            {
                "period_type": p["period_type"],
                "period_id": p["period_id"],
                "listings_targeted": p["listings_targeted"],
                "listings_processed": p["listings_processed"],
                "listings_processed_count": len(p["listings_processed"]),
                "source_freshness_counts": p["source_freshness_counts"],
                "deploy": p["deploy"],
            }
            for p in periods
        ],
        "failures": all_failures,
        "ok": not all_failures,
    }

    print()
    print(json.dumps(summary))
    return 0 if not all_failures else 1


if __name__ == "__main__":
    sys.exit(main())
