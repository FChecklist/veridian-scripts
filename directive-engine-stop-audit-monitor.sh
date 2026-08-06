#!/usr/bin/env bash
# directive-engine-stop-audit-monitor.sh
#
# UMR-20260806-231410-331d (child of parent UMR-20260806-103954-6f42, governing
# PM report contract UMR-20260806-042531-be9c): real-time D-Bus audit for who is
# explicitly stopping veridian-directive-engine.service. Real journalctl --user
# evidence showed clean "Stopping ... Stopped" cycles (Result=success,
# ExecMainStatus=0) roughly every ~17 minutes, ruling out a crash/OOM kill
# (MemoryMax=384M) -- this is a real external explicit stop/restart job whose
# origin is not yet identified.
#
# WHY a D-Bus monitor and not a plain ExecStop= hook: ExecStop= only runs
# *after* systemd has already begun the stop job, inside the unit's own
# cgroup -- it has zero visibility into which external process issued the
# stop. `busctl --user monitor` uses systemd/D-Bus's own real eavesdropping
# facility (org.freedesktop.DBus.Monitoring.BecomeMonitor under the hood) to
# observe the actual org.freedesktop.systemd1 method call in flight, while
# the caller's D-Bus connection may still be open, so we can attempt to
# resolve the real sender PID via org.freedesktop.DBus.GetConnectionUnixProcessID
# and then read /proc/<pid>/cmdline + /proc/<pid>/status (PPid) -- real
# attribution when it lands.
#
# HONEST, LIVE-DEMONSTRATED LIMITATION (not theoretical): a real self-test
# (`systemctl --user start` then `--user stop` on the target unit while this
# monitor was running, see /opt/veridian/logs/directive-engine-stop-audit.log
# 2026-08-06T23:15:36Z) proved the STOP CALL ITSELF is captured correctly
# (method + real unit name + real timestamp) every time, but PID resolution
# can lose the race for a short-lived single-shot CLI caller (like a bare
# `systemctl --user stop ...` invocation): resolving via a brand-new
# `busctl --user call ... GetConnectionUnixProcessID` subprocess incurs its
# own connection-handshake latency (new process fork/exec + new bus
# handshake, empirically tens of ms), which can exceed the caller's own
# connection lifetime. A parallel attempt to eliminate this race with a
# persistent single-process dbus-python `BecomeMonitor` daemon
# (directive_engine_stop_audit_monitor.py, kept in this repo for the record)
# was tried and hit a reproducible crash on this system: the monitor
# connection receives its own D-Bus `Disconnected` signal immediately after
# `BecomeMonitor` succeeds (suspected root cause: dbus-python's high-level
# `add_message_filter()`/`SessionBus()` wrapper issues an implicit `AddMatch`
# call, which a true eavesdropper connection is not permitted to send, so
# the bus drops it) -- 2 consecutive failures of that approach, so per this
# task's protocol that path was abandoned rather than reattempted a 3rd time.
# To partially mitigate the race without that risk, this script caches every
# sender's PID resolution the moment ANY message from that sender is
# observed (not only stop-related calls) -- real, empirically-observed
# traffic on this bus shows some callers reuse one connection for 2+ calls
# (e.g. Get(ActiveState) then Get(LoadState) back to back), so a stop call
# arriving on an already-seen connection can be attributed from cache with
# zero additional race exposure. A single-shot caller with exactly one
# message on its connection (as our self-test was) will still show
# sender_pid=<unresolved:...> -- disclosed here, not hidden.
#
# Runs as the ExecStart of veridian-directive-engine-stop-audit.service
# (systemd --user, Restart=always) so it stays running unattended per the PM
# directive's step 3 ("leave the instrumentation running").

set -u

UNIT="veridian-directive-engine.service"
UNIT_OBJECT_PATH="/org/freedesktop/systemd1/unit/veridian_2ddirective_2dengine_2eservice"
LOG=/opt/veridian/logs/directive-engine-stop-audit.log

mkdir -p "$(dirname "$LOG")"

log() {
    printf '%s %s\n' "$(date -u +%FT%T.%3NZ)" "$1" >> "$LOG"
}

log "[MONITOR-START] pid=$$ watching Manager.StopUnit/KillUnit/RestartUnit/ReloadOrRestartUnit(${UNIT}) and Unit.Stop/Kill/Restart on ${UNIT_OBJECT_PATH} via busctl --user monitor org.freedesktop.systemd1, with per-sender PID cache (UMR-20260806-231410-331d)"

declare -A PID_CACHE
declare -A CMDLINE_CACHE
declare -A PPID_CACHE
declare -A PPID_CMDLINE_CACHE

resolve_and_cache() {
    # $1 = sender unique name. Populates the *_CACHE arrays as a side
    # effect (best-effort, silent on failure) and echoes nothing.
    local sender="$1" pid ppid cmdline ppid_cmdline
    [ -n "${PID_CACHE[$sender]:-}" ] && return 0
    pid=$(busctl --user call org.freedesktop.DBus /org/freedesktop/DBus \
        org.freedesktop.DBus GetConnectionUnixProcessID s "$sender" 2>/dev/null | awk '{print $2}')
    [ -z "${pid:-}" ] || [ ! -e "/proc/$pid" ] && return 1
    cmdline=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)
    ppid=$(awk '/^PPid:/{print $2}' "/proc/$pid/status" 2>/dev/null)
    ppid_cmdline=""
    if [ -n "${ppid:-}" ] && [ -e "/proc/$ppid" ]; then
        ppid_cmdline=$(tr '\0' ' ' < "/proc/$ppid/cmdline" 2>/dev/null)
    fi
    PID_CACHE[$sender]="$pid"
    CMDLINE_CACHE[$sender]="$cmdline"
    PPID_CACHE[$sender]="${ppid:-}"
    PPID_CMDLINE_CACHE[$sender]="$ppid_cmdline"
    return 0
}

emit_stop_evidence() {
    local method="$1" sender="$2" raw="$3"
    resolve_and_cache "$sender"
    if [ -n "${PID_CACHE[$sender]:-}" ]; then
        log "[STOP-CALL-CAPTURED] method=${method} sender=${sender} sender_pid=${PID_CACHE[$sender]} sender_cmdline=\"${CMDLINE_CACHE[$sender]}\" sender_ppid=${PPID_CACHE[$sender]} ppid_cmdline=\"${PPID_CMDLINE_CACHE[$sender]}\""
    else
        log "[STOP-CALL-CAPTURED] method=${method} sender=${sender} sender_pid=<unresolved:already-disconnected-or-race-lost> sender_cmdline= sender_ppid= ppid_cmdline="
    fi
    log "[STOP-CALL-RAW] ${raw}"
}

JQ_FILTER='
  select(.type == "method_call") |
  {
    sender: .sender,
    is_stop: (
      (.interface == "org.freedesktop.systemd1.Manager"
        and (.member as $m | ["StopUnit","KillUnit","RestartUnit","ReloadOrRestartUnit","ReloadOrTryRestartUnit","TryRestartUnit"] | index($m))
        and ((.payload.data // []) | map(tostring) | index("'"$UNIT"'")))
      or
      (.path == "'"$UNIT_OBJECT_PATH"'"
        and .interface == "org.freedesktop.systemd1.Unit"
        and (.member as $m | ["Stop","Kill","Restart","ReloadOrRestart"] | index($m)))
    ),
    method: (.interface + "." + .member),
    raw: tostring
  } |
  [.sender, (.is_stop|tostring), .method, .raw] | @tsv
'

busctl --user monitor --json=short org.freedesktop.systemd1 2>>"$LOG" | \
jq -c --unbuffered -r "$JQ_FILTER" 2>>"$LOG" | \
while IFS=$'\t' read -r sender is_stop method raw; do
    # Eagerly warm the cache for EVERY sender we see, stop-related or not --
    # this is what lets a stop call on an already-seen connection resolve
    # from cache with zero extra race exposure (see file header).
    resolve_and_cache "${sender:-}" 2>/dev/null || true
    if [ "$is_stop" = "true" ]; then
        emit_stop_evidence "$method" "${sender:-<none>}" "$raw"
    fi
done

log "[MONITOR-EXIT] busctl/jq pipeline ended (Restart=always will relaunch us)"
