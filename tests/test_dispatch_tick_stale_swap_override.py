#!/usr/bin/env python3
"""Real tests for dispatch-tick.py's has_free_slot_with_stale_swap_override()
(task-20260813-205525-close-fake-progress-md-only-prs-317-321, closing PR
#317's real gap -- that PR itself shipped zero lines of code, PROGRESS.md
only, and was closed unmerged).

Real gap this closes: dispatch_core.has_free_slot()'s swap_backoff check
(dispatch_core.py, frozen under the narrow 2026-08-08 stop-work order) is a
STATIC SwapFree/SwapTotal occupancy ratio -- Linux never proactively
reclaims swap pages once written, so a single past spike latches that gate
closed FOREVER, even with abundant real MemAvailable and zero ongoing swap
I/O (real evidence: UMR-20260813-155201-da76). resource_governor.py already
carries a real, narrow, activity-based override for exactly this
(_override_stale_swap_backoff(), see tests/test_stale_swap_ratchet_override.py)
but it was only ever wired into resource_governor.dispatch_one()'s own
umr_tasks queue -- dispatch-tick.py's own 3 real spawn call sites
(supervisor_sweep_tick, gap_queue_tick, module_queue_tick) called
dispatch_core.has_free_slot() directly and never went through it, so a
stale swap ratchet could still permanently wedge THOSE 3 dispatch paths.
This file proves (1) the new shared helper reuses the override correctly at
the unit level, and (2) wired against the REAL resource_governor override
machinery (never reimplemented here) with real fixture /proc files, the
latch genuinely cannot stick closed when swap I/O is quiet and MemAvailable
is healthy, and genuinely stays closed when swap I/O is actually active --
i.e. it can reopen, and it does not fail open unconditionally.

Every /proc read in the integration section goes through resource_governor's
own env-overridable path constants against real temp fixture files -- never
the live host's real /proc (same convention as
tests/test_stale_swap_ratchet_override.py). dispatch_core.has_free_slot_detail()
itself is not env-overridable (reads real host /proc directly, by design --
untouched by this task, still frozen), so it is mocked at the seam in the
integration tests, exactly as tests/test_stale_swap_ratchet_override.py's own
end-to-end section already does.
"""
import datetime
import importlib.util
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _load_dispatch_tick(env=None):
    # dispatch-tick.py does plain top-level `import dispatch_core` /
    # `import resource_governor` -- pop any cached copies first so a fresh
    # load picks up this test's own env-var fixture overrides (same
    # convention tests/test_dispatch_tick_owner_dispatch_reconciliation.py
    # already uses), and restore the real env afterwards.
    old_env = {}
    for k, v in (env or {}).items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v
    sys.modules.pop("resource_governor", None)
    sys.modules.pop("dispatch_core", None)
    try:
        spec = importlib.util.spec_from_file_location(
            "dispatch_tick_stale_swap_test", os.path.join(SCRIPTS_DIR, "dispatch-tick.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


REAL_SLOT_DETAIL = {"check": "swap_backoff", "swap_used_pct": 0.8149, "threshold_pct": 0.8}


# ---------------------------------------------------------------------------
# 1. Unit-level: the helper must call has_free_slot_detail() then feed the
#    result through resource_governor's override, and return ITS verdict.
# ---------------------------------------------------------------------------

def test_delegates_to_dispatch_core_and_returns_override_verdict_true(monkeypatch):
    mod = _load_dispatch_tick()
    monkeypatch.setattr(mod.dispatch_core, "has_free_slot_detail",
                         lambda cap=None: (False, dict(REAL_SLOT_DETAIL)))
    monkeypatch.setattr(mod.resource_governor, "_override_stale_swap_backoff",
                         lambda slot_ok, slot_detail: (True, {"check": "swap_backoff_override_stale_ratchet"}))
    assert mod.has_free_slot_with_stale_swap_override() is True
    print("PASS: test_delegates_to_dispatch_core_and_returns_override_verdict_true")


def test_delegates_to_dispatch_core_and_returns_override_verdict_false(monkeypatch):
    mod = _load_dispatch_tick()
    monkeypatch.setattr(mod.dispatch_core, "has_free_slot_detail",
                         lambda cap=None: (False, {"check": "cap_exhausted", "running_worker_count": 5, "cap": 5}))
    calls = []

    def _override(slot_ok, slot_detail):
        calls.append((slot_ok, slot_detail))
        return slot_ok, slot_detail  # cap_exhausted must pass through unchanged

    monkeypatch.setattr(mod.resource_governor, "_override_stale_swap_backoff", _override)
    assert mod.has_free_slot_with_stale_swap_override() is False
    assert calls == [(False, {"check": "cap_exhausted", "running_worker_count": 5, "cap": 5})]
    print("PASS: test_delegates_to_dispatch_core_and_returns_override_verdict_false")


def test_passes_cap_through_to_has_free_slot_detail(monkeypatch):
    mod = _load_dispatch_tick()
    seen_caps = []
    monkeypatch.setattr(mod.dispatch_core, "has_free_slot_detail",
                         lambda cap=None: (seen_caps.append(cap), (True, {"check": "ok"}))[1])
    monkeypatch.setattr(mod.resource_governor, "_override_stale_swap_backoff",
                         lambda slot_ok, slot_detail: (slot_ok, slot_detail))
    assert mod.has_free_slot_with_stale_swap_override(cap=7) is True
    assert seen_caps == [7]
    print("PASS: test_passes_cap_through_to_has_free_slot_detail")


# ---------------------------------------------------------------------------
# 2. Integration: real resource_governor override machinery (real vmstat/
#    meminfo fixture files, never mocked) wired through the helper -- proves
#    the latch genuinely cannot stick closed when swap I/O is quiet and
#    MemAvailable is healthy, and genuinely still blocks when swap is
#    actively being written to (i.e. it is a real gate, not a bypass).
# ---------------------------------------------------------------------------

def _write_meminfo(path, mem_total_kb, mem_available_kb, swap_total_kb, swap_free_kb):
    with open(path, "w") as f:
        f.write(f"MemTotal:       {mem_total_kb} kB\n")
        f.write(f"MemAvailable:   {mem_available_kb} kB\n")
        f.write(f"SwapTotal:       {swap_total_kb} kB\n")
        f.write(f"SwapFree:        {swap_free_kb} kB\n")


def _write_vmstat(path, pswpin, pswpout):
    with open(path, "w") as f:
        f.write(f"pswpin {pswpin}\n")
        f.write(f"pswpout {pswpout}\n")


REAL_MEM_TOTAL_KB = 15982916
REAL_MEM_AVAILABLE_KB = 11299132
REAL_SWAP_TOTAL_KB = 4194300
REAL_SWAP_FREE_KB = 775980  # -> swap_used_pct = 0.8149, a stale ratchet per real SPEC evidence


def test_real_override_reopens_dispatch_when_swap_quiet_and_memory_healthy(tmp_path, monkeypatch):
    """The exact real fingerprint from the governing SPEC's own evidence:
    SwapFree byte-frozen (stale ratchet), MemAvailable ~11.3GB genuinely
    free, real swap I/O confirmed quiet across two samples -- the latch
    must NOT stick closed; a real spawn must be allowed."""
    env = {
        "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR,
        "VERIDIAN_GOVERNOR_PROC_MEMINFO": str(tmp_path / "meminfo"),
        "VERIDIAN_GOVERNOR_PROC_VMSTAT": str(tmp_path / "vmstat"),
        "VERIDIAN_GOVERNOR_SWAP_ACTIVITY_STATE": str(tmp_path / "swap-activity-state.json"),
        "VERIDIAN_GOVERNOR_SWAP_ACTIVITY_MIN_INTERVAL_S": "0",
    }
    mod = _load_dispatch_tick(env)
    _write_meminfo(str(tmp_path / "meminfo"), REAL_MEM_TOTAL_KB, REAL_MEM_AVAILABLE_KB,
                    REAL_SWAP_TOTAL_KB, REAL_SWAP_FREE_KB)
    _write_vmstat(str(tmp_path / "vmstat"), pswpin=501, pswpout=1079)

    monkeypatch.setattr(mod.dispatch_core, "has_free_slot_detail",
                         lambda cap=None: (False, dict(REAL_SLOT_DETAIL)))

    # First real call establishes the swap-activity baseline sample (a bare
    # cold start must never itself dispatch -- see
    # tests/test_stale_swap_ratchet_override.py's own cold-start test).
    first = mod.has_free_slot_with_stale_swap_override()
    assert first is False

    # so=0/si=0 real delta over a real elapsed window -> genuinely quiet.
    second = mod.has_free_slot_with_stale_swap_override()
    assert second is True, "stale swap ratchet must not permanently wedge dispatch-tick.py's own call sites"
    print("PASS: test_real_override_reopens_dispatch_when_swap_quiet_and_memory_healthy")


def test_real_override_still_blocks_when_swap_actually_active(tmp_path, monkeypatch):
    """Same stale-looking swap_used_pct, but real swap I/O is genuinely
    ongoing this time -- must still block, proving this is a real gate and
    not an unconditional bypass."""
    env = {
        "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR,
        "VERIDIAN_GOVERNOR_PROC_MEMINFO": str(tmp_path / "meminfo"),
        "VERIDIAN_GOVERNOR_PROC_VMSTAT": str(tmp_path / "vmstat"),
        "VERIDIAN_GOVERNOR_SWAP_ACTIVITY_STATE": str(tmp_path / "swap-activity-state.json"),
        "VERIDIAN_GOVERNOR_SWAP_ACTIVITY_MIN_INTERVAL_S": "0",
    }
    mod = _load_dispatch_tick(env)
    vmstat_path = str(tmp_path / "vmstat")
    _write_meminfo(str(tmp_path / "meminfo"), REAL_MEM_TOTAL_KB, REAL_MEM_AVAILABLE_KB,
                    REAL_SWAP_TOTAL_KB, REAL_SWAP_FREE_KB)
    _write_vmstat(vmstat_path, pswpin=501, pswpout=1079)

    monkeypatch.setattr(mod.dispatch_core, "has_free_slot_detail",
                         lambda cap=None: (False, dict(REAL_SLOT_DETAIL)))

    assert mod.has_free_slot_with_stale_swap_override() is False  # cold-start baseline
    _write_vmstat(vmstat_path, pswpin=520, pswpout=1600)  # real, ongoing swap-out

    assert mod.has_free_slot_with_stale_swap_override() is False, \
        "must still defer while real swap I/O is actively ongoing"
    print("PASS: test_real_override_still_blocks_when_swap_actually_active")


def test_real_override_never_touches_non_swap_backoff_gates(tmp_path, monkeypatch):
    """cap_exhausted must pass straight through even with abundant memory
    and quiet swap -- this override is narrowly scoped to swap_backoff
    only, same real constraint resource_governor's own override enforces."""
    env = {
        "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR,
        "VERIDIAN_GOVERNOR_PROC_MEMINFO": str(tmp_path / "meminfo"),
        "VERIDIAN_GOVERNOR_PROC_VMSTAT": str(tmp_path / "vmstat"),
        "VERIDIAN_GOVERNOR_SWAP_ACTIVITY_STATE": str(tmp_path / "swap-activity-state.json"),
        "VERIDIAN_GOVERNOR_SWAP_ACTIVITY_MIN_INTERVAL_S": "0",
    }
    mod = _load_dispatch_tick(env)
    _write_meminfo(str(tmp_path / "meminfo"), REAL_MEM_TOTAL_KB, REAL_MEM_AVAILABLE_KB,
                    REAL_SWAP_TOTAL_KB, REAL_SWAP_FREE_KB)
    _write_vmstat(str(tmp_path / "vmstat"), pswpin=0, pswpout=0)

    monkeypatch.setattr(mod.dispatch_core, "has_free_slot_detail",
                         lambda cap=None: (False, {"check": "cap_exhausted", "running_worker_count": 5, "cap": 5}))

    mod.has_free_slot_with_stale_swap_override()
    assert mod.has_free_slot_with_stale_swap_override() is False
    print("PASS: test_real_override_never_touches_non_swap_backoff_gates")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            import inspect
            kwargs = {}
            if "tmp_path" in inspect.signature(t).parameters:
                import tempfile
                import pathlib
                td = tempfile.mkdtemp()
                kwargs["tmp_path"] = pathlib.Path(td)
            if "monkeypatch" in inspect.signature(t).parameters:
                import _pytest.monkeypatch
                kwargs["monkeypatch"] = _pytest.monkeypatch.MonkeyPatch()
            t(**kwargs)
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR: {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
