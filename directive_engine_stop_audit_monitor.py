#!/usr/bin/env python3
"""directive_engine_stop_audit_monitor.py

UMR-20260806-231410-331d (child of parent UMR-20260806-103954-6f42, governing
PM report contract UMR-20260806-042531-be9c): real-time D-Bus audit for who is
explicitly stopping veridian-directive-engine.service.

Real journalctl --user evidence showed clean "Stopping ... Stopped" cycles
(Result=success, ExecMainStatus=0) roughly every ~17 minutes -- rules out a
crash/OOM kill (MemoryMax=384M). This is a real external explicit
stop/restart job whose origin is not yet identified.

WHY this (and why it replaces this task's first bash+busctl-subprocess
attempt): a plain ExecStop= hook only runs *after* systemd has already begun
the stop job, inside the unit's own cgroup -- zero visibility into which
external process issued the stop. A first version of this instrumentation
(directive-engine-stop-audit-monitor.sh, still present in this repo for the
record) used `busctl --user monitor` piped through `jq`, then spawned a
*brand-new* `busctl --user call ... GetConnectionUnixProcessID` subprocess
per captured event to resolve the sender's PID. A real, live self-test
(`systemctl --user start` then `--user stop` on the target unit, see
/opt/veridian/logs/directive-engine-stop-audit.log 2026-08-06T23:15:36Z)
proved that subprocess-spawn + fresh-bus-connection-handshake latency loses
the race almost every time for a short-lived CLI caller: the StopUnit call
was captured correctly (method + unit name), but the resolve attempt came
back `sender_pid=<unresolved:already-disconnected>` -- the caller's
connection had already closed by the time the new busctl process finished
authenticating.

This version fixes that by keeping the entire pipeline in ONE persistent
Python process with TWO already-open D-Bus connections:
  1. `monitor_bus` -- turned into a real eavesdropping monitor via the bus
     driver's own `org.freedesktop.DBus.Monitoring.BecomeMonitor` method
     (the same real mechanism `busctl monitor`/`dbus-monitor` use internally
     -- a genuine systemd/D-Bus audit facility, not a home-grown poll loop).
  2. `resolver_bus` -- a normal, already-connected bus handle used only to
     call GetConnectionUnixProcessID the instant a matching message is
     observed, with no subprocess spawn and no new connection handshake --
     just an in-process method call over a socket that is already open.
This removes essentially all of the latency that defeated the first attempt.

Runs as the ExecStart of veridian-directive-engine-stop-audit.service
(systemd --user, Restart=always) so it stays running unattended per the PM
directive's step 3 ("leave the instrumentation running").
"""
import datetime
import os

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

UNIT = "veridian-directive-engine.service"
UNIT_OBJECT_PATH = "/org/freedesktop/systemd1/unit/veridian_2ddirective_2dengine_2eservice"
MANAGER_STOP_MEMBERS = {
    "StopUnit", "KillUnit", "RestartUnit",
    "ReloadOrRestartUnit", "ReloadOrTryRestartUnit", "TryRestartUnit",
}
UNIT_STOP_MEMBERS = {"Stop", "Kill", "Restart", "ReloadOrRestart"}

LOG_PATH = "/opt/veridian/logs/directive-engine-stop-audit.log"


def log(line):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(f"{ts} {line}\n")
    print(f"{ts} {line}", flush=True)


def read_proc_info(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().replace(b"\x00", b" ").decode(errors="replace").strip()
    except OSError:
        cmdline = ""
    ppid = ""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line_ in f:
                if line_.startswith("PPid:"):
                    ppid = line_.split()[1]
                    break
    except OSError:
        pass
    ppid_cmdline = ""
    if ppid:
        try:
            with open(f"/proc/{ppid}/cmdline", "rb") as f:
                ppid_cmdline = f.read().replace(b"\x00", b" ").decode(errors="replace").strip()
        except OSError:
            pass
    return cmdline, ppid, ppid_cmdline


def handle_message(bus, message, resolver):
    # Every real branch below is guarded -- an uncaught exception inside a
    # dbus-python message filter callback risks corrupting the C-level
    # connection state (observed live during development: an unguarded
    # version of this callback made the whole process disappear with zero
    # traceback, see PROGRESS.md's dated note), so this filter must never
    # let a Python exception cross back into libdbus.
    try:
        return _handle_message_inner(message, resolver)
    except Exception as exc:  # noqa: BLE001 -- real, deliberate catch-all
        try:
            log(f"[MONITOR-CALLBACK-ERROR] {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED


def _handle_message_inner(message, resolver):
    if message.get_type() != 1:  # 1 == METHOD_CALL
        return dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED
    interface = message.get_interface()
    member = message.get_member()
    path = message.get_path()
    matched_method = None
    if interface == "org.freedesktop.systemd1.Manager" and member in MANAGER_STOP_MEMBERS:
        try:
            args = message.get_args_list()
        except Exception:
            args = []
        if args and str(args[0]) == UNIT:
            matched_method = f"Manager.{member}"
    elif interface == "org.freedesktop.systemd1.Unit" and member in UNIT_STOP_MEMBERS and path == UNIT_OBJECT_PATH:
        matched_method = f"Unit.{member}"

    if matched_method is None:
        return dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED

    sender = message.get_sender()
    pid = None
    try:
        pid = resolver.call_blocking(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "GetConnectionUnixProcessID", "s", [sender],
        )
    except dbus.exceptions.DBusException as exc:
        log(f"[STOP-CALL-CAPTURED] method={matched_method} sender={sender} "
            f"sender_pid=<unresolved:{exc.get_dbus_name()}> sender_cmdline= sender_ppid= ppid_cmdline=")
        return dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED

    cmdline, ppid, ppid_cmdline = read_proc_info(pid)
    log(f"[STOP-CALL-CAPTURED] method={matched_method} sender={sender} "
        f'sender_pid={pid} sender_cmdline="{cmdline}" sender_ppid={ppid} ppid_cmdline="{ppid_cmdline}"')
    try:
        log(f"[STOP-CALL-RAW] path={path} interface={interface} member={member} args={message.get_args_list()!r}")
    except Exception:
        pass
    return dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED


def main():
    DBusGMainLoop(set_as_default=True)
    monitor_bus = dbus.SessionBus(private=True)
    resolver_bus = dbus.SessionBus(private=True)

    monitor_bus.call_blocking(
        "org.freedesktop.DBus", "/org/freedesktop/DBus",
        "org.freedesktop.DBus.Monitoring", "BecomeMonitor", "asu", [[], 0],
    )

    monitor_bus.add_message_filter(lambda bus, msg: handle_message(bus, msg, resolver_bus))

    log(f"[MONITOR-START] pid={os.getpid()} persistent dbus-python BecomeMonitor + pre-opened "
        f"resolver connection watching Manager.{{{','.join(sorted(MANAGER_STOP_MEMBERS))}}}({UNIT}) "
        f"and Unit.{{{','.join(sorted(UNIT_STOP_MEMBERS))}}} on {UNIT_OBJECT_PATH} "
        f"(UMR-20260806-231410-331d; replaces the subprocess-per-event busctl+jq version after a "
        f"live self-test proved that version loses the PID-resolution race)")

    loop = GLib.MainLoop()
    try:
        loop.run()
    finally:
        log("[MONITOR-EXIT] GLib main loop ended (Restart=always will relaunch us)")


if __name__ == "__main__":
    main()
