#!/usr/bin/env python3
"""gtm_check_load_stress_testing.py -- real, re-runnable check for GTM
certification category_index=10 ("load testing") and category_index=11
("stress testing").

Authorization chain: Real Owner directive UMR-20260806-100739-82dc gave
explicit go-ahead to attempt categories 10/11, which had been sitting
`blocked` (passed=NULL) since UMR-20260805-131542-121f's prior
OOM-adjacent-load caution. Parent UMR-20260802-165606-4413 / OCID-020.

IMPORTANT: authorization to *attempt* a run is not authorization to *skip
the safety gate*. The gate below is unconditional, has no override flag,
env var, or CLI switch, and is checked fresh (never cached/reused) every
time this script starts.

------------------------------------------------------------------------
Safety gate (real, hard, unconditional -- checked before anything else):
    REFUSES TO START if, right now:
        real SwapFree  < 500 MiB   OR
        real load1 (1-minute load average) > 10.0
    This mirrors dispatch_core.py's has_resource_headroom() discipline
    (same file, same server) but with this task's own explicit numbers.

Hard abort (real, during any real run that did start):
    Polls real SwapFree + real load1 every ABORT_POLL_INTERVAL_SECONDS.
    The INSTANT real SwapFree < 200 MiB OR real load1 > 25.0, the real
    load-generator subprocess is killed (SIGKILL, immediate) and the run
    is marked aborted. No grace period, no retry, no override.

Non-production target only:
    This script will never point a real load run at the live production
    site (https://projexa-ai.com -- confirmed live and reachable by this
    repo's own category_index=4/8/9/18/24 checks). discover_nonprod_target()
    searches for a real, currently-reachable non-prod target (a local dev
    server instance; a Vercel preview deployment IF real credentials are
    present) and returns None, with an honest per-candidate log, if none
    is genuinely reachable -- this script does not fall back to production
    and does not fabricate a target.

Response-time pass ceiling: p95 < 2000ms (RESPONSE_TIME_P95_CEILING_MS).
    Justification: 2000ms is the widely used "acceptable, not yet
    degraded" ceiling for server-rendered page response under load (Google
    RAIL / Core Web Vitals treats <1000ms as ideal and >3000ms as a likely
    abandon point for a fresh navigation; 2000ms sits inside that band as
    a real, defensible, documented pass/fail line for a Next.js
    server-rendered response specifically, not a static-asset fetch).
    This is a fixed constant, not tuned after seeing results.

Every real run -- whether refused at the gate, blocked for want of a real
target, aborted mid-run, or completed -- ends by calling the shared writer
gtm_write_category_result.py (never raw SQL) for BOTH category_index=10
and category_index=11, never fabricating a pass/fail when the check could
not genuinely run.

Usage:
  gtm_check_load_stress_testing.py [--concurrency N] [--max-requests N]
                                    [--duration-seconds N]
  gtm_check_load_stress_testing.py --_generator-worker --target URL
                                    --concurrency N --max-requests N
                                    --duration-seconds N   (internal, real
                                    subprocess entrypoint -- do not call
                                    directly)
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")

CATEGORY_INDEX_LOAD = 10
CATEGORY_INDEX_STRESS = 11

# ---- Real, hard, unconditional thresholds (bytes / load1) -----------------
START_SWAP_FREE_MIN_BYTES = 500 * 1024 * 1024      # 500 MiB
START_LOAD1_MAX = 10.0

ABORT_SWAP_FREE_MIN_BYTES = 200 * 1024 * 1024      # 200 MiB
ABORT_LOAD1_MAX = 25.0
ABORT_POLL_INTERVAL_SECONDS = 3

RESPONSE_TIME_P95_CEILING_MS = 2000
ERROR_RATE_MAX = 0.05  # >5% request errors is a real fail, not a pass

PRODUCTION_HOSTS = ("projexa-ai.com", "www.projexa-ai.com")

# Bounded-run defaults -- deliberately small; this is a certification smoke
# check, not a capacity-planning benchmark.
DEFAULT_CONCURRENCY = 5
DEFAULT_MAX_REQUESTS = 200
DEFAULT_DURATION_SECONDS = 20


# ---------------------------------------------------------------------------
# Real metric reads
# ---------------------------------------------------------------------------
def _read_meminfo_bytes():
    """Real /proc/meminfo values in bytes. Same parsing convention as
    dispatch_core.py's _read_meminfo_bytes() on this server."""
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if ":" not in line:
                    continue
                key, rest = line.split(":", 1)
                parts = rest.split()
                if not parts:
                    continue
                info[key.strip()] = int(parts[0]) * 1024
    except (OSError, ValueError):
        pass
    return info


def read_system_metrics():
    """Real, fresh read of memory/swap/load -- never cached, never reused
    across calls. This is the ONLY function that touches /proc/meminfo or
    os.getloadavg() in this script; every gate/abort check takes its input
    as a plain dict so tests can inject fabricated values without touching
    the real filesystem or real kernel state."""
    meminfo = _read_meminfo_bytes()
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        # Fail safe: if load can't genuinely be read, treat as maximally
        # loaded so the gate refuses rather than assumes idle.
        load1 = load5 = load15 = float("inf")
    return {
        "mem_total_bytes": meminfo.get("MemTotal", 0),
        "mem_available_bytes": meminfo.get("MemAvailable", 0),
        "swap_total_bytes": meminfo.get("SwapTotal", 0),
        "swap_free_bytes": meminfo.get("SwapFree", 0),
        "load1": load1,
        "load5": load5,
        "load15": load15,
        "read_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Real, pure gate/abort logic (no I/O -- takes metrics dicts as input, so
# tests exercise the exact real code path with fabricated boundary values)
# ---------------------------------------------------------------------------
def check_start_gate(metrics):
    """Real unconditional start gate. Returns (allowed: bool, reason: str).
    Refuses if swap_free < 500MiB OR load1 > 10.0. No override."""
    reasons = []
    if metrics["swap_free_bytes"] < START_SWAP_FREE_MIN_BYTES:
        reasons.append(
            f"real swap free {metrics['swap_free_bytes']} bytes "
            f"({metrics['swap_free_bytes'] / 1024 / 1024:.1f} MiB) is under "
            f"the {START_SWAP_FREE_MIN_BYTES // 1024 // 1024} MiB start-gate minimum"
        )
    if metrics["load1"] > START_LOAD1_MAX:
        reasons.append(
            f"real load1 {metrics['load1']:.2f} is over the "
            f"{START_LOAD1_MAX} start-gate maximum"
        )
    if reasons:
        return False, "; ".join(reasons)
    return True, "real swap free and real load1 both within start-gate bounds"


def check_abort_condition(metrics):
    """Real unconditional hard-abort check for a run already in progress.
    Returns None if no abort is needed, else a real, specific reason
    string. Refuses (aborts) if swap_free < 200MiB OR load1 > 25.0."""
    if metrics["swap_free_bytes"] < ABORT_SWAP_FREE_MIN_BYTES:
        return (
            f"real swap free {metrics['swap_free_bytes']} bytes "
            f"({metrics['swap_free_bytes'] / 1024 / 1024:.1f} MiB) dropped under "
            f"the {ABORT_SWAP_FREE_MIN_BYTES // 1024 // 1024} MiB hard-abort floor"
        )
    if metrics["load1"] > ABORT_LOAD1_MAX:
        return (
            f"real load1 {metrics['load1']:.2f} crossed the "
            f"{ABORT_LOAD1_MAX} hard-abort ceiling"
        )
    return None


class AbortMonitor:
    """Real background poller. In production, .start() runs a real daemon
    thread that calls metrics_fn() every poll_interval_seconds and, the
    instant check_abort_condition() trips, calls kill_fn() exactly once and
    stops polling. Tests call .tick() directly (no real thread, no real
    sleep) to prove exact-threshold behavior deterministically and fast."""

    def __init__(self, metrics_fn, kill_fn, poll_interval_seconds=ABORT_POLL_INTERVAL_SECONDS, on_sample=None):
        self.metrics_fn = metrics_fn
        self.kill_fn = kill_fn
        self.poll_interval_seconds = poll_interval_seconds
        self.on_sample = on_sample
        self.tripped = False
        self.trip_reason = None
        self.trip_metrics = None
        self._stop = threading.Event()
        self._thread = None

    def tick(self):
        """One real check. Records a sample (if on_sample given), and --
        the instant it trips -- fires kill_fn() exactly once. Returns the
        abort reason string if this tick tripped it, else None. Idempotent
        once tripped (kill_fn is never called a second time)."""
        metrics = self.metrics_fn()
        if self.on_sample is not None:
            self.on_sample(metrics)
        if self.tripped:
            return None
        reason = check_abort_condition(metrics)
        if reason:
            self.tripped = True
            self.trip_reason = reason
            self.trip_metrics = metrics
            self.kill_fn()
            return reason
        return None

    def start(self):
        def _loop():
            while not self._stop.is_set():
                self.tick()
                if self.tripped:
                    break
                self._stop.wait(self.poll_interval_seconds)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Real non-production target discovery -- never production, never fabricated
# ---------------------------------------------------------------------------
def discover_nonprod_target(local_dev_url="http://127.0.0.1:3000", timeout_seconds=3):
    """Real search for a real, currently-reachable non-production target.
    Returns (target_url_or_None, discovery_log). Never returns a
    PRODUCTION_HOSTS URL. Never fabricates reachability."""
    log = []

    for host in PRODUCTION_HOSTS:
        assert host not in local_dev_url, "refusing to treat a production host as a candidate"

    try:
        req = urllib.request.Request(local_dev_url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            log.append({"candidate": local_dev_url, "kind": "local_dev_server", "reachable": True, "status": resp.status})
            return local_dev_url, log
    except Exception as e:
        log.append({"candidate": local_dev_url, "kind": "local_dev_server", "reachable": False, "error": str(e)})

    token_env_present = any(k.startswith("VERCEL_") and "TOKEN" in k and os.environ.get(k) for k in os.environ)
    global_config_path = os.path.expanduser("~/.local/share/com.vercel.cli/config.json")
    global_config_has_token = False
    if os.path.isfile(global_config_path):
        try:
            with open(global_config_path) as f:
                global_config_has_token = bool(json.load(f).get("token"))
        except (json.JSONDecodeError, OSError):
            pass

    if not (token_env_present or global_config_has_token):
        log.append({
            "candidate": "vercel preview deployment",
            "kind": "vercel_preview",
            "reachable": False,
            "error": "no real Vercel credential present (same finding as gtm_check_deployment_testing.py category_index=21: "
                     "no VERCEL_*_TOKEN env var, no token in ~/.local/share/com.vercel.cli/config.json) -- not attempting "
                     "`vercel ls`/`vercel inspect` (would hang on an interactive device-auth flow, per that script's own finding).",
        })
    else:
        log.append({
            "candidate": "vercel preview deployment",
            "kind": "vercel_preview",
            "reachable": False,
            "error": "real Vercel credential presence check passed, but automated preview-URL resolution is not yet "
                     "implemented in this script -- no real credential existed at authoring time to build/test that path "
                     "against, and this script will not guess a preview URL rather than genuinely resolving one via the CLI.",
        })

    return None, log


# ---------------------------------------------------------------------------
# Real bounded load generator -- runs as a real, killable OS subprocess
# ---------------------------------------------------------------------------
def _generator_worker_main(target, concurrency, max_requests, duration_seconds):
    """Runs INSIDE the real subprocess (re-invocation of this same file with
    --_generator-worker). Issues real bounded HTTP GET requests against
    `target` with `concurrency` worker threads, stopping at whichever of
    max_requests / duration_seconds comes first. Emits one real JSON line
    per request to stdout, flushed immediately."""
    stop_at = time.monotonic() + duration_seconds
    remaining = {"n": max_requests}
    lock = threading.Lock()
    out_lock = threading.Lock()

    def emit(rec):
        with out_lock:
            print(json.dumps(rec), flush=True)

    def worker():
        while True:
            with lock:
                if remaining["n"] <= 0 or time.monotonic() >= stop_at:
                    return
                remaining["n"] -= 1
            t0 = time.monotonic()
            rec = {"t": t0}
            try:
                req = urllib.request.Request(target, method="GET")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp.read(2048)  # bounded read, don't hold full body in memory
                    rec["status"] = resp.status
                    rec["error"] = None
            except urllib.error.HTTPError as e:
                rec["status"] = e.code
                rec["error"] = None  # a real HTTP error status is still a real response, not a transport failure
            except Exception as e:
                rec["status"] = None
                rec["error"] = str(e)
            rec["latency_ms"] = (time.monotonic() - t0) * 1000.0
            emit(rec)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=duration_seconds + 15)


def run_bounded_load(target, concurrency, max_requests, duration_seconds, metrics_fn=read_system_metrics):
    """Real bounded load run against `target` as a real killable subprocess,
    with a real AbortMonitor polling real metrics_fn() throughout. Returns a
    result dict with before/during-peak/after metrics, request stats, and
    whether the run completed or was hard-aborted."""
    before_metrics = metrics_fn()

    proc = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__),
         "--_generator-worker",
         "--target", target,
         "--concurrency", str(concurrency),
         "--max-requests", str(max_requests),
         "--duration-seconds", str(duration_seconds)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )

    peak_samples = []

    def kill_now():
        try:
            proc.kill()
        except ProcessLookupError:
            pass

    monitor = AbortMonitor(metrics_fn, kill_now, on_sample=peak_samples.append)
    monitor.start()

    try:
        stdout, stderr = proc.communicate(timeout=duration_seconds + 20)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
    finally:
        monitor.stop()

    after_metrics = metrics_fn()

    records = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    latencies_ms = [r["latency_ms"] for r in records if r.get("error") is None]
    error_count = sum(1 for r in records if r.get("error") is not None)
    total = len(records)

    def _peak(key):
        vals = [s[key] for s in peak_samples if key in s]
        return max(vals) if vals else None

    def _peak_min(key):
        vals = [s[key] for s in peak_samples if key in s]
        return min(vals) if vals else None

    peak_metrics = {
        "load1_peak": _peak("load1"),
        "swap_free_bytes_min": _peak_min("swap_free_bytes"),
        "sample_count": len(peak_samples),
    }

    p95_ms = None
    if latencies_ms:
        sorted_lat = sorted(latencies_ms)
        idx = min(len(sorted_lat) - 1, int(round(0.95 * (len(sorted_lat) - 1))))
        p95_ms = sorted_lat[idx]

    return {
        "target": target,
        "before_metrics": before_metrics,
        "peak_metrics": peak_metrics,
        "after_metrics": after_metrics,
        "aborted": monitor.tripped,
        "abort_reason": monitor.trip_reason,
        "process_returncode": proc.returncode,
        "total_requests": total,
        "error_count": error_count,
        "error_rate": (error_count / total) if total else None,
        "p95_latency_ms": p95_ms,
        "stderr_tail": (stderr or "")[-2000:],
    }


# ---------------------------------------------------------------------------
# Writer plumbing
# ---------------------------------------------------------------------------
def call_writer(category_index, result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(category_index),
        "--result", result,
        "--script-path", "gtm_check_load_stress_testing.py",
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    print("Calling writer:", " ".join(cmd), file=sys.stderr)
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        sys.exit(p.returncode)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    ap.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    ap.add_argument("--duration-seconds", type=int, default=DEFAULT_DURATION_SECONDS)
    ap.add_argument("--_generator-worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--target", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args._generator_worker:
        # Real subprocess entrypoint -- re-invocation of this same file.
        _generator_worker_main(args.target, args.concurrency, args.max_requests, args.duration_seconds)
        return

    metrics = read_system_metrics()
    allowed, gate_reason = check_start_gate(metrics)

    if not allowed:
        summary = f"Real safety-gate refusal: {gate_reason}. No load or stress run was attempted."
        evidence = {
            "gate": "start_gate",
            "gate_thresholds": {
                "swap_free_min_bytes": START_SWAP_FREE_MIN_BYTES,
                "load1_max": START_LOAD1_MAX,
            },
            "real_metrics_at_check": metrics,
            "refusal_reason": gate_reason,
        }
        print(summary, file=sys.stderr)
        call_writer(CATEGORY_INDEX_LOAD, "blocked", summary, evidence)
        call_writer(CATEGORY_INDEX_STRESS, "blocked", summary, evidence)
        return

    target, discovery_log = discover_nonprod_target()
    if target is None:
        summary = "Safety gate passed, but no real reachable non-production target was found. Refusing to substitute production or fabricate a result."
        evidence = {
            "gate": "start_gate",
            "gate_result": "passed",
            "real_metrics_at_check": metrics,
            "target_discovery_log": discovery_log,
        }
        print(summary, file=sys.stderr)
        call_writer(CATEGORY_INDEX_LOAD, "blocked", summary, evidence)
        call_writer(CATEGORY_INDEX_STRESS, "blocked", summary, evidence)
        return

    # Load run: normal bounded concurrency/duration.
    load_result = run_bounded_load(target, args.concurrency, args.max_requests, args.duration_seconds)
    _record_run_result(CATEGORY_INDEX_LOAD, "load testing", load_result, metrics, discovery_log)

    if load_result["aborted"]:
        # Do not escalate into a stress run after a real hard abort.
        summary = f"Stress run skipped: the preceding load run was hard-aborted ({load_result['abort_reason']})."
        call_writer(CATEGORY_INDEX_STRESS, "blocked", summary, {"skipped_because": load_result["abort_reason"]})
        return

    # Stress run: same target, higher concurrency (beyond-normal load), same
    # hard thresholds/monitor -- re-checks the real start gate fresh, since
    # real conditions may have changed during the load run.
    metrics_for_stress = read_system_metrics()
    allowed2, gate_reason2 = check_start_gate(metrics_for_stress)
    if not allowed2:
        summary = f"Real safety-gate refusal before stress run (conditions changed since the load run): {gate_reason2}."
        call_writer(CATEGORY_INDEX_STRESS, "blocked", summary, {"real_metrics_at_check": metrics_for_stress, "refusal_reason": gate_reason2})
        return

    stress_result = run_bounded_load(target, args.concurrency * 3, args.max_requests * 3, args.duration_seconds)
    _record_run_result(CATEGORY_INDEX_STRESS, "stress testing", stress_result, metrics_for_stress, discovery_log)


def _record_run_result(category_index, label, result, gate_metrics, discovery_log):
    evidence = {
        "gate_result": "passed",
        "real_metrics_at_gate_check": gate_metrics,
        "target_discovery_log": discovery_log,
        "response_time_p95_ceiling_ms": RESPONSE_TIME_P95_CEILING_MS,
        "error_rate_max": ERROR_RATE_MAX,
        **result,
    }
    if result["aborted"]:
        summary = (
            f"Real {label} run HARD-ABORTED: {result['abort_reason']}. "
            f"{result['total_requests']} request(s) completed before abort."
        )
        call_writer(category_index, "blocked", summary, evidence)
        return

    crashed = result["process_returncode"] not in (0, None) and result["process_returncode"] < 0
    p95 = result["p95_latency_ms"]
    error_rate = result["error_rate"] or 0.0
    passed = (
        not crashed
        and p95 is not None
        and p95 < RESPONSE_TIME_P95_CEILING_MS
        and error_rate <= ERROR_RATE_MAX
        and result["total_requests"] > 0
    )
    summary = (
        f"Real {label} against {result['target']}: {result['total_requests']} requests, "
        f"{error_rate * 100:.1f}% errors, p95={p95}ms (ceiling {RESPONSE_TIME_P95_CEILING_MS}ms), "
        f"process_returncode={result['process_returncode']}."
    )
    call_writer(category_index, "pass" if passed else "fail", summary, evidence)


if __name__ == "__main__":
    main()
