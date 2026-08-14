#!/usr/bin/env python3
"""reap_stale_test_scratch.py -- periodic backstop that removes stale test
harness scratch under /tmp, so a leak like the one that caused
UMR-20260814-033442-c885 (P0: /dev/sda1 100% full, all 17 veridian-worker
units failed at preflight, resource_governor.py --query-umr returned
count=0 for every real status -- the whole AI OS halted) can never again
silently refill the disk between the individual test-file fixes and a human
noticing.

ROOT CAUSE this backstops (see the real fixes in test_pm_sentinel_tick.py,
test_resource_governor_queue_management.py, tests/test_rule7_completion_evidence.py
for the actual per-test-file leak fixes -- this script is the SECOND layer,
not a substitute for those): every real test harness that seeds an isolated
copy of superboss-register.sqlite drops it under a real, recognizable /tmp
prefix. Even with every current leak fixed at the source, a future test
added without the same discipline (or a test process genuinely killed by
something outside its own control -- OOM-kill, SIGKILL, a host reboot mid-run)
can still leave scratch behind. This reaper is the guard that keeps that
from ever again silently growing to 122GB unnoticed.

POLICY:
  - Only ever touches /tmp (never /opt/veridian or any real data path).
  - Only entries whose basename matches one of the real, confirmed literal
    leak prefixes from the RCA (see PREFIXES/EXACT_NAMES below) -- never a
    bare wildcard sweep of /tmp, which could delete something unrelated.
  - Only entries older than --min-age-hours (default 2h, per the task
    spec's own "startup/periodic reaper that removes stale (>2h) test
    scratch" requirement) by mtime -- a real, currently-running test's own
    scratch dir is always younger than that (these test suites run in
    seconds to low tens of seconds; the pm-sentinel-tick suite, the
    slowest of the three, is ~31s end to end).
  - Best-effort open-handle check via `lsof` before deleting each
    candidate (same safety discipline the PM-Desktop Sentinel's own manual
    mitigation used) -- skips (does not delete, logs why) anything lsof
    reports as still open. Never fatal if lsof itself is unavailable/fails
    (fails open on the CHECK, not on the deletion decision -- an unwritable
    lsof binary must not silently disable this whole reaper).

Usage:
    reap_stale_test_scratch.py [--dry-run] [--min-age-hours 2] [--tmp-dir /tmp]

Exit codes: 0 always (a reaper that fails open must never itself become a
new source of unit failures) -- real errors are printed to stderr and
counted in the summary line, not raised.
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

# Real, literal leak-signature prefixes from the RCA (UMR-20260814-033442-c885).
# Directory prefixes (matched via basename.startswith()):
DIR_PREFIXES = (
    "pm_sentinel_tick_",
    "rule7-real-persist-test-",
    "rule7-non-completed-test-",
    "rg_queue_mgmt_test_",
)
# Standalone files found by the real du -sx -m sweep (not from a mkdtemp
# prefix -- fixed filenames some ad-hoc/manual debugging session left
# behind; no committed call site produces these, see progress notes).
EXACT_FILE_NAMES = (
    "sbr-test-copy.sqlite",
    "dedup_test.sqlite",
)


def _has_open_handle(path):
    """Best-effort: True only if lsof explicitly confirms an open handle.
    Any failure to run lsof at all (not installed, permission, timeout)
    fails OPEN (returns False -- does not block deletion) so a missing
    lsof binary can never silently turn this reaper into a no-op."""
    try:
        result = subprocess.run(
            ["lsof", "-t", path], capture_output=True, text=True, timeout=5,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def _candidates(tmp_dir):
    try:
        entries = os.listdir(tmp_dir)
    except OSError as e:
        print(f"reap_stale_test_scratch: cannot list {tmp_dir}: {e}", file=sys.stderr)
        return
    for name in entries:
        if name.startswith(DIR_PREFIXES) or name in EXACT_FILE_NAMES:
            yield os.path.join(tmp_dir, name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                         help="print what would be removed, delete nothing")
    parser.add_argument("--min-age-hours", type=float, default=2.0,
                         help="only remove entries older than this (default 2h)")
    parser.add_argument("--tmp-dir", default="/tmp",
                         help="scan root (default /tmp; override for testing)")
    args = parser.parse_args()

    cutoff = time.time() - (args.min_age_hours * 3600)
    removed, skipped_young, skipped_open, errors = 0, 0, 0, 0
    reclaimed_bytes = 0

    for path in _candidates(args.tmp_dir):
        try:
            mtime = os.lstat(path).st_mtime
        except OSError as e:
            print(f"reap_stale_test_scratch: stat failed for {path}: {e}", file=sys.stderr)
            errors += 1
            continue
        if mtime >= cutoff:
            skipped_young += 1
            continue
        if _has_open_handle(path):
            print(f"reap_stale_test_scratch: SKIP (open handle) {path}", file=sys.stderr)
            skipped_open += 1
            continue

        size = 0
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                for root, _dirs, files in os.walk(path):
                    for f in files:
                        try:
                            size += os.lstat(os.path.join(root, f)).st_size
                        except OSError:
                            pass
            else:
                size = os.lstat(path).st_size
        except OSError:
            pass

        if args.dry_run:
            print(f"reap_stale_test_scratch: WOULD REMOVE {path} (~{size / 1e6:.1f}MB)")
            removed += 1
            reclaimed_bytes += size
            continue

        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            print(f"reap_stale_test_scratch: removed {path} (~{size / 1e6:.1f}MB)")
            removed += 1
            reclaimed_bytes += size
        except OSError as e:
            print(f"reap_stale_test_scratch: FAILED to remove {path}: {e}", file=sys.stderr)
            errors += 1

    print(
        f"reap_stale_test_scratch: {removed} removed (~{reclaimed_bytes / 1e6:.1f}MB), "
        f"{skipped_young} too young, {skipped_open} open-handle skips, {errors} errors"
        f"{' [DRY RUN]' if args.dry_run else ''}"
    )
    # Real errors are surfaced above but never make this reaper itself a
    # unit failure -- see module docstring.
    sys.exit(0)


if __name__ == "__main__":
    main()
