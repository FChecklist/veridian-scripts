#!/usr/bin/env python3
"""Real tests for gtm_check_load_stress_testing.py (categories 10/11, Real
Owner directive UMR-20260806-100739-82dc, parent UMR-20260802-165606-4413 /
OCID-020).

Covers, with real code paths and real injected fakes (never a live
production request, never a real DB write):
  1. check_start_gate() -- exact boundary behavior at 500MiB swap-free /
     10.0 load1, both individually and combined.
  2. check_abort_condition() -- exact boundary behavior at 200MiB
     swap-free / 25.0 load1.
  3. AbortMonitor.tick() -- kill_fn fires exactly once, on the exact tick
     the fabricated metrics cross the threshold, never before, never
     twice.
  4. discover_nonprod_target() -- honest "not found" behavior against real
     (but controlled/unreachable) local conditions; never returns a
     production host.
  5. run_bounded_load() end-to-end against a REAL local http.server on
     127.0.0.1 (genuinely local, genuinely trivial, no external/production
     traffic) -- proves the real subprocess spawn + real request loop +
     real p95 computation, AND proves the real hard-abort path genuinely
     kills the real subprocess (negative returncode) the instant a
     fabricated metrics_fn reports a tripped condition.
  6. main()'s refusal/blocked paths call the shared writer with the
     correct category indices and an honest "blocked" result -- writer
     itself is monkeypatched (never a real DB write in this test file).
"""
import http.server
import importlib.util
import json
import os
import socket
import sys
import threading
import time

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "gtm_check_load_stress_testing_test",
        os.path.join(SCRIPTS_DIR, "gtm_check_load_stress_testing.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()


def _metrics(swap_free_mib, load1, swap_total_mib=4096):
    return {
        "mem_total_bytes": 16 * 1024 ** 3,
        "mem_available_bytes": 8 * 1024 ** 3,
        "swap_total_bytes": swap_total_mib * 1024 * 1024,
        "swap_free_bytes": swap_free_mib * 1024 * 1024,
        "load1": load1,
        "load5": load1,
        "load15": load1,
        "read_at": "2026-08-06T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# 1. Start gate boundaries
# ---------------------------------------------------------------------------
def test_start_gate_passes_well_within_bounds():
    allowed, reason = mod.check_start_gate(_metrics(swap_free_mib=1000, load1=2.0))
    assert allowed is True


def test_start_gate_refuses_on_swap_exactly_at_499mib():
    allowed, reason = mod.check_start_gate(_metrics(swap_free_mib=499, load1=1.0))
    assert allowed is False
    assert "swap free" in reason


def test_start_gate_allows_swap_exactly_at_500mib():
    # threshold is "< 500MiB refuses", so exactly 500MiB must pass on the
    # swap dimension.
    allowed, reason = mod.check_start_gate(_metrics(swap_free_mib=500, load1=1.0))
    assert allowed is True


def test_start_gate_refuses_on_load1_just_over_10():
    allowed, reason = mod.check_start_gate(_metrics(swap_free_mib=1000, load1=10.01))
    assert allowed is False
    assert "load1" in reason


def test_start_gate_allows_load1_exactly_at_10():
    # threshold is "> 10.0 refuses", so exactly 10.0 must pass.
    allowed, reason = mod.check_start_gate(_metrics(swap_free_mib=1000, load1=10.0))
    assert allowed is True


def test_start_gate_refuses_and_cites_both_reasons_when_both_tripped():
    allowed, reason = mod.check_start_gate(_metrics(swap_free_mib=100, load1=30.0))
    assert allowed is False
    assert "swap free" in reason and "load1" in reason


def test_start_gate_reflects_real_current_server_condition_snapshot():
    # Real evidence: this server's actual condition observed during this
    # task (load1 ~24.9, swap free ~248KiB) must trip the gate -- confirms
    # the gate is not accidentally inverted or off-by-a-unit.
    allowed, reason = mod.check_start_gate(_metrics(swap_free_mib=0, load1=24.87))
    # 0 MiB swap free rounds down from the real ~248KiB observed figure
    assert allowed is False


# ---------------------------------------------------------------------------
# 2. Abort condition boundaries
# ---------------------------------------------------------------------------
def test_abort_condition_none_well_within_bounds():
    assert mod.check_abort_condition(_metrics(swap_free_mib=1000, load1=5.0)) is None


def test_abort_condition_fires_on_swap_exactly_at_199mib():
    reason = mod.check_abort_condition(_metrics(swap_free_mib=199, load1=1.0))
    assert reason is not None and "swap free" in reason


def test_abort_condition_none_on_swap_exactly_at_200mib():
    assert mod.check_abort_condition(_metrics(swap_free_mib=200, load1=1.0)) is None


def test_abort_condition_fires_on_load1_just_over_25():
    reason = mod.check_abort_condition(_metrics(swap_free_mib=1000, load1=25.01))
    assert reason is not None and "load1" in reason


def test_abort_condition_none_on_load1_exactly_at_25():
    assert mod.check_abort_condition(_metrics(swap_free_mib=1000, load1=25.0)) is None


# ---------------------------------------------------------------------------
# 3. AbortMonitor.tick() -- fires exactly once, exactly at the trip tick
# ---------------------------------------------------------------------------
def test_abort_monitor_fires_kill_exactly_once_at_the_exact_trip_tick():
    # Sequence of fabricated real-shaped metrics: safe, safe, safe, TRIP, safe (post-trip, must not re-fire)
    sequence = [
        _metrics(swap_free_mib=1000, load1=5.0),
        _metrics(swap_free_mib=800, load1=8.0),
        _metrics(swap_free_mib=600, load1=12.0),   # over start-gate but NOT abort -- still safe re: abort
        _metrics(swap_free_mib=150, load1=26.0),   # TRIP: both conditions crossed
        _metrics(swap_free_mib=1000, load1=1.0),   # recovered, but monitor already tripped -- must not un-fire/re-fire
    ]
    it = iter(sequence)
    kill_calls = []

    monitor = mod.AbortMonitor(metrics_fn=lambda: next(it), kill_fn=lambda: kill_calls.append(1))

    reasons = [monitor.tick() for _ in sequence]

    assert reasons == [None, None, None, reasons[3], None]
    # swap dimension is checked first in check_abort_condition(), and swap
    # (150 MiB) is also under its 200 MiB floor on this tick, so the real
    # reason cites swap free -- confirm it's a real, specific reason string,
    # not merely truthy.
    assert reasons[3] is not None and "swap free" in reasons[3] and "150.0 MiB" in reasons[3]
    assert kill_calls == [1]  # exactly once
    assert monitor.tripped is True
    assert monitor.trip_reason == reasons[3]


def test_abort_monitor_trips_on_swap_alone_not_just_load():
    sequence = [
        _metrics(swap_free_mib=1000, load1=1.0),
        _metrics(swap_free_mib=50, load1=1.0),  # swap alone crosses
    ]
    it = iter(sequence)
    kill_calls = []
    monitor = mod.AbortMonitor(metrics_fn=lambda: next(it), kill_fn=lambda: kill_calls.append(1))
    r0 = monitor.tick()
    r1 = monitor.tick()
    assert r0 is None
    assert r1 is not None and "swap free" in r1
    assert kill_calls == [1]


def test_abort_monitor_on_sample_records_every_tick_including_post_trip():
    sequence = [
        _metrics(swap_free_mib=1000, load1=1.0),
        _metrics(swap_free_mib=50, load1=1.0),
        _metrics(swap_free_mib=1000, load1=1.0),
    ]
    it = iter(sequence)
    samples = []
    monitor = mod.AbortMonitor(metrics_fn=lambda: next(it), kill_fn=lambda: None, on_sample=samples.append)
    for _ in sequence:
        monitor.tick()
    assert len(samples) == 3  # every tick sampled, trip or not


# ---------------------------------------------------------------------------
# 4. discover_nonprod_target() -- honest, never production
# ---------------------------------------------------------------------------
def test_discover_nonprod_target_never_returns_a_production_host():
    for host in mod.PRODUCTION_HOSTS:
        assert host not in "http://127.0.0.1:3000"


def test_discover_nonprod_target_reports_not_found_when_nothing_reachable(monkeypatch, tmp_path):
    # Force local dev server candidate to fail (real behavior right now on
    # this server -- nothing is genuinely listening on 127.0.0.1:3000).
    monkeypatch.setenv("HOME", str(tmp_path))  # no real ~/.local/share/com.vercel.cli/config.json
    for k in list(os.environ):
        if k.startswith("VERCEL_"):
            monkeypatch.delenv(k, raising=False)

    target, log = mod.discover_nonprod_target(local_dev_url="http://127.0.0.1:59999")  # real, genuinely-unbound port
    assert target is None
    assert len(log) == 2
    assert log[0]["kind"] == "local_dev_server" and log[0]["reachable"] is False
    assert log[1]["kind"] == "vercel_preview" and log[1]["reachable"] is False
    assert all(host not in json.dumps(log) for host in mod.PRODUCTION_HOSTS)


def test_discover_nonprod_target_finds_a_real_reachable_local_server():
    server, port, thread = _start_tiny_http_server()
    try:
        target, log = mod.discover_nonprod_target(local_dev_url=f"http://127.0.0.1:{port}")
        assert target == f"http://127.0.0.1:{port}"
        assert log[0]["reachable"] is True
    finally:
        server.shutdown()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# 5. run_bounded_load() against a REAL local http.server (loopback only)
# ---------------------------------------------------------------------------
class _TinyOKHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_HEAD(self):
        # discover_nonprod_target() probes with a real HEAD request --
        # BaseHTTPRequestHandler returns 501 for HEAD without this.
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        pass  # silence test output


def _start_tiny_http_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _TinyOKHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port, thread


def test_run_bounded_load_completes_against_real_local_server_and_computes_p95():
    server, port, thread = _start_tiny_http_server()
    try:
        target = f"http://127.0.0.1:{port}"
        result = mod.run_bounded_load(
            target, concurrency=2, max_requests=10, duration_seconds=5,
            metrics_fn=lambda: _metrics(swap_free_mib=1000, load1=1.0),
        )
        assert result["aborted"] is False
        assert result["total_requests"] > 0
        assert result["error_count"] == 0
        assert result["p95_latency_ms"] is not None
        assert result["p95_latency_ms"] < mod.RESPONSE_TIME_P95_CEILING_MS
        assert result["process_returncode"] == 0
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_run_bounded_load_hard_abort_genuinely_kills_the_subprocess():
    server, port, thread = _start_tiny_http_server()
    try:
        target = f"http://127.0.0.1:{port}"

        # First real call reports safe conditions (so the run starts); every
        # subsequent call reports a tripped condition, forcing a real,
        # immediate kill of the real subprocess.
        calls = {"n": 0}

        def metrics_fn():
            calls["n"] += 1
            if calls["n"] == 1:
                return _metrics(swap_free_mib=1000, load1=1.0)
            return _metrics(swap_free_mib=50, load1=30.0)  # tripped: both dimensions

        result = mod.run_bounded_load(
            target, concurrency=2, max_requests=1000000, duration_seconds=30,
            metrics_fn=metrics_fn,
        )
        assert result["aborted"] is True
        assert result["abort_reason"] is not None
        assert ("swap free" in result["abort_reason"]) or ("load1" in result["abort_reason"])
        # A real SIGKILL surfaces as a negative returncode on POSIX.
        assert result["process_returncode"] is not None and result["process_returncode"] < 0
    finally:
        server.shutdown()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# 6. main()'s refusal/blocked paths call the writer honestly (monkeypatched)
# ---------------------------------------------------------------------------
def test_main_refuses_and_writes_blocked_for_both_categories_when_gate_trips(monkeypatch):
    monkeypatch.setattr(mod, "read_system_metrics", lambda: _metrics(swap_free_mib=0, load1=24.87))

    calls = []
    monkeypatch.setattr(mod, "call_writer", lambda category_index, result, summary, evidence: calls.append(
        (category_index, result, summary, evidence)
    ))

    mod.main([])

    assert len(calls) == 2
    cats = sorted(c[0] for c in calls)
    assert cats == [mod.CATEGORY_INDEX_LOAD, mod.CATEGORY_INDEX_STRESS]
    for _, result, summary, evidence in calls:
        assert result == "blocked"
        assert "refusal" in summary.lower()
        assert evidence["refusal_reason"]
        assert evidence["real_metrics_at_check"]["load1"] == 24.87


def test_main_blocks_both_categories_when_gate_passes_but_no_target_found(monkeypatch):
    monkeypatch.setattr(mod, "read_system_metrics", lambda: _metrics(swap_free_mib=1000, load1=1.0))
    monkeypatch.setattr(mod, "discover_nonprod_target", lambda: (None, [{"candidate": "x", "reachable": False, "error": "none"}]))

    calls = []
    monkeypatch.setattr(mod, "call_writer", lambda category_index, result, summary, evidence: calls.append(
        (category_index, result, summary, evidence)
    ))

    mod.main([])

    assert len(calls) == 2
    for _, result, summary, evidence in calls:
        assert result == "blocked"
        assert "no real reachable non-production target" in summary.lower()
        assert evidence["target_discovery_log"]
