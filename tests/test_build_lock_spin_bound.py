#!/usr/bin/env python3
"""Real test for the anti-spin safeguard the PM approval of proposal 62
attached as a mandatory NEW requirement on top of the already-merged
UMR-20260806-123316-cf9f / PR #172 (see PROGRESS.md and
SPEC_VERIFICATION_2026-08-06T234542Z.md for the full trail): a task that
repeatedly loses the quality-gate.sh build lock must not be able to
requeue forever -- it must eventually surface a real, terminal gate
failure, and that bound must be proven by a real test, not just asserted.

quality-gate.sh's own acquire_build_lock_fd() (already implemented and
merged, unchanged by this file) already encodes this bound: a per-task
consecutive-loss counter (persisted in
$TASK_DIR/.build-lock-loss-count) escalates to a single long-wait fallback
on the 4th consecutive loss, and if THAT also fails, returns rc=2 -- which
quality-gate.sh's own build-gate branch (see the `elif`/`else` after
`acquire_build_lock_fd` in quality-gate.sh) records as a real failed gate
(OVERALL=1) rather than calling requeue-build-lock-contended a 5th time.
Neither existing build-lock test file
(tests/test_build_lock_contended_requeue.py, which exercises the CLI +
real dispatcher pickup, or tests/test_build_lock_liveness_guard.py, which
exercises the separate stuck-holder liveness guard) exercises this
specific escalation/termination bound directly. This file closes that gap.

Real, not mocked: extracts the REAL acquire_build_lock_fd() function body
verbatim out of the live quality-gate.sh (by real brace-depth counting on
the actual file text, not a hand-copied duplicate -- if the real function
is ever edited, this test starts exercising the new real text
automatically next run) and executes it, unmodified, in a real bash
subprocess against a real flock held by a real competitor subprocess. Only
the wait-second knobs it already exposes as plain shell variables
(BUILD_LOCK_SHORT_WAIT_SECONDS / BUILD_LOCK_LONG_WAIT_SECONDS) are turned
down from the real 20s/700s production defaults so this test finishes in
seconds -- same "compress the real thresholds' shape in time" convention
tests/test_build_lock_liveness_guard.py already established. Those two
production values are themselves already independently verified live (see
PR #172's commit message: "a real scratch workspace run against a real
held /tmp/veridian-quality-gate-build.lock confirmed ... the 4th-
consecutive-loss 700s fallback ... behave[s] as designed") -- this file's
job is proving the *bound on cycle count* holds, at any wait-second scale,
not re-proving the production timing constants.
"""
import os
import subprocess
import sys
import tempfile
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUALITY_GATE_SH = os.path.join(REPO_ROOT, "quality-gate.sh")

FLOCK_BIN = "/usr/bin/flock" if os.path.exists("/usr/bin/flock") else "flock"


def _extract_function(source_text, name):
    """Real brace-depth extraction of a bash function's exact source text,
    tolerant of arbitrary leading indentation (acquire_build_lock_fd is
    defined nested inside an `if` block in the real file, not at column
    0). Never hand-duplicates the function body."""
    lines = source_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == f"{name}() {{":
            start = i
            break
    assert start is not None, f"could not find real `{name}() {{` in quality-gate.sh -- has it been renamed/refactored?"
    depth = 0
    end = None
    for i in range(start, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if i > start and depth == 0:
            end = i
            break
    assert end is not None, f"could not find the matching closing brace for real `{name}()`"
    return "\n".join(lines[start:end + 1])


def _build_harness(short_wait, long_wait, lock_file, loss_count_file):
    with open(QUALITY_GATE_SH) as f:
        real_fn_text = _extract_function(f.read(), "acquire_build_lock_fd")
    return f"""#!/bin/bash
set -uo pipefail
BUILD_LOCK_FILE={lock_file!r}
BUILD_LOCK_SHORT_WAIT_SECONDS={short_wait}
BUILD_LOCK_LONG_WAIT_SECONDS={long_wait}
BUILD_LOCK_LOSS_COUNT_FILE={loss_count_file!r}

{real_fn_text}

acquire_build_lock_fd
echo "RC=$?"
"""


def _run_once(short_wait, long_wait, lock_file, loss_count_file):
    """One real, independent invocation -- mirrors one real quality-gate.sh
    process (a fresh shell each time, exactly like a real requeued task's
    fresh systemd unit run), reading/writing the real loss-count file on
    disk, never carrying in-process state between calls."""
    script = _build_harness(short_wait, long_wait, lock_file, loss_count_file)
    t0 = time.monotonic()
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    elapsed = time.monotonic() - t0
    rc_line = [l for l in proc.stdout.splitlines() if l.startswith("RC=")]
    assert rc_line, f"harness produced no RC= line -- stdout={proc.stdout!r} stderr={proc.stderr!r}"
    rc = int(rc_line[-1].split("=", 1)[1])
    return rc, elapsed


def _read_loss_count(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read().strip()


def test_repeated_contention_is_bounded_then_surfaces_real_failure_never_a_5th_cycle():
    with tempfile.TemporaryDirectory() as d:
        lock_file = os.path.join(d, "build.lock")
        loss_count_file = os.path.join(d, ".build-lock-loss-count")
        short_wait, long_wait = "0.3", "0.6"

        # Real competitor: holds the real lock for the whole first phase of
        # this test via a real `flock` subprocess (same real binary, same
        # real file, same real advisory-lock semantics quality-gate.sh's
        # own build step contends against in production).
        competitor = subprocess.Popen(
            [FLOCK_BIN, lock_file, "-c", "sleep 30"], start_new_session=True,
        )
        try:
            time.sleep(0.15)  # let the competitor genuinely take the lock first

            results = []
            for attempt in range(1, 5):
                rc, elapsed = _run_once(short_wait, long_wait, lock_file, loss_count_file)
                loss_count_now = _read_loss_count(loss_count_file)
                results.append((attempt, rc, elapsed, loss_count_now))

            # Attempts 1-3: real short-wait losses (rc=1, caller requeues),
            # loss counter genuinely incrementing 1, 2, 3 on real disk state.
            for attempt, rc, elapsed, loss_count_now in results[:3]:
                assert rc == 1, f"attempt {attempt}: expected rc=1 (short-wait loss), got rc={rc}"
                assert loss_count_now == str(attempt), (
                    f"attempt {attempt}: real loss-count file should read {attempt!r}, "
                    f"got {loss_count_now!r} -- counter not genuinely persisting/incrementing"
                )

            # Attempt 4: 4th CONSECUTIVE loss -- must escalate to the real
            # long-wait fallback instead of a 4th short-wait requeue cycle,
            # and since the competitor is still genuinely holding the lock,
            # that long wait also genuinely fails -> rc=2, a real terminal
            # gate failure, NOT another requeue-able rc=1. This is the
            # actual bound: at most 4 consecutive contended cycles ever
            # happen before a real blocker surfaces -- never a 5th.
            attempt4, rc4, elapsed4, loss_count4 = results[3]
            assert rc4 == 2, (
                f"4th consecutive loss must surface as a real terminal failure (rc=2), "
                f"not another requeue-able loss -- got rc={rc4}. If this is rc=1, the "
                f"escalation never fired and a task COULD requeue indefinitely."
            )
            assert loss_count4 == "4", f"expected real loss-count file to read '4' after the 4th consecutive loss, got {loss_count4!r}"
            # Real timing proof the long-wait branch actually ran (not just
            # another short wait that happened to return something else):
            long_wait_f = float(long_wait)
            short_wait_f = float(short_wait)
            assert elapsed4 >= long_wait_f * 0.8, (
                f"attempt 4 took {elapsed4:.2f}s, expected roughly the real "
                f"{long_wait_f}s long-wait fallback duration, not just another "
                f"{short_wait_f}s short wait"
            )

            print(
                "PASS: 4 consecutive real contended attempts -> rc sequence "
                f"{[r[1] for r in results]}, loss-count sequence "
                f"{[r[3] for r in results]}, attempt 4 elapsed={elapsed4:.2f}s "
                f"(long_wait={long_wait_f}s) -- bounded at 4, never a 5th cycle"
            )
        finally:
            competitor.kill()
            competitor.wait(timeout=5)


def test_contention_clearing_lets_a_later_attempt_genuinely_succeed_and_resets_counter():
    """The bound above must not be a permanent lockout once the real
    contention actually clears -- genuine forward progress must still be
    possible. Real competitor releases the lock; a subsequent real
    invocation must acquire it immediately (rc=0) and the real loss-count
    file must be cleared, proving the task is not left permanently
    penalized by its earlier losses."""
    with tempfile.TemporaryDirectory() as d:
        lock_file = os.path.join(d, "build.lock")
        loss_count_file = os.path.join(d, ".build-lock-loss-count")
        short_wait, long_wait = "0.3", "0.6"

        # Seed real prior-loss state, exactly as it would exist on disk
        # after 3 real prior consecutive losses.
        with open(loss_count_file, "w") as f:
            f.write("3")

        competitor = subprocess.Popen(
            [FLOCK_BIN, lock_file, "-c", "sleep 0.4"], start_new_session=True,
        )
        try:
            time.sleep(0.15)
            competitor.wait(timeout=5)  # real: contention genuinely clears

            rc, elapsed = _run_once(short_wait, long_wait, lock_file, loss_count_file)
            assert rc == 0, f"expected real success (rc=0) once contention genuinely clears, got rc={rc}"
            assert not os.path.exists(loss_count_file), (
                "real loss-count file must be removed on a genuine acquire -- otherwise "
                "a task that finally makes progress stays permanently one loss away from "
                "prematurely re-triggering the long-wait escalation"
            )
            print(f"PASS: real acquire succeeded in {elapsed:.2f}s once contention cleared, loss-count reset")
        finally:
            competitor.kill()
            try:
                competitor.wait(timeout=5)
            except Exception:
                pass


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__} -> {e}")
        except Exception as e:
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"ERROR: {t.__name__} -> {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)
