#!/usr/bin/env python3
"""Real tests for directive_engine_stop_audit_monitor.py -- persistent
dbus-python D-Bus eavesdropping monitor that logs who explicitly stops
veridian-directive-engine.service.

This environment has a REAL, live D-Bus session bus (DBUS_SESSION_BUS_ADDRESS
is set and functional), so these tests exercise real dbus.SessionBus
connections, a real org.freedesktop.DBus.Monitoring.BecomeMonitor call, and a
real GetConnectionUnixProcessID resolver call against our own live connection
(whose PID we independently know via os.getpid()) -- nothing about D-Bus
itself is stubbed.

The only thing stubbed is the incoming dbus.lowlevel.Message object passed
into handle_message()/_handle_message_inner(): constructing a real
dbus.lowlevel.MethodCallMessage with a controlled, unroutable destination and
reliably capturing it via eavesdropping inside a synchronous pytest test is
inherently racy (the module's own docstring documents this exact race as the
reason a previous subprocess-based version of this instrumentation was
rejected) -- so a small real-attribute fake Message class stands in for "an
inbound D-Bus method-call envelope", which is the genuine external boundary
here (the systemd/D-Bus wire protocol we don't own), while every real
function under test (_handle_message_inner, handle_message, read_proc_info,
log, main's real wiring) runs unmodified.

LOG_PATH is monkeypatched to a real, throwaway tmp_path file for every test
-- the real default (/opt/veridian/logs/directive-engine-stop-audit.log) is
never opened or written to.
"""
import importlib.util
import os
import subprocess
import sys
import time
import uuid

import dbus
import dbus.lowlevel
import pytest
from gi.repository import GLib

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SUT_PATH = os.path.join(SCRIPTS_DIR, "directive_engine_stop_audit_monitor.py")


def _load_fresh():
    name = f"sut_directive_engine_stop_audit_monitor_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, SUT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod(tmp_path):
    m = _load_fresh()
    m.LOG_PATH = str(tmp_path / "directive-engine-stop-audit.log")
    return m


class FakeMessage:
    """Stands in for a real dbus.lowlevel.Message -- exposes exactly the
    subset of the real Message API the SUT calls (get_type, get_interface,
    get_member, get_path, get_sender, get_args_list)."""

    def __init__(self, type_=1, interface=None, member=None, path=None,
                 sender=":1.42", args=None, args_raises=False, type_raises=False):
        self._type = type_
        self._interface = interface
        self._member = member
        self._path = path
        self._sender = sender
        self._args = args if args is not None else []
        self._args_raises = args_raises
        self._type_raises = type_raises

    def get_type(self):
        if self._type_raises:
            raise RuntimeError("simulated get_type() failure")
        return self._type

    def get_interface(self):
        return self._interface

    def get_member(self):
        return self._member

    def get_path(self):
        return self._path

    def get_sender(self):
        return self._sender

    def get_args_list(self):
        if self._args_raises:
            raise RuntimeError("simulated get_args_list() failure")
        return self._args


@pytest.fixture(scope="module")
def real_resolver_bus():
    """A real, live D-Bus session-bus connection used purely as the
    resolver -- GetConnectionUnixProcessID against our OWN unique name is a
    real round trip through the real bus daemon whose answer we can
    independently verify against os.getpid()."""
    bus = dbus.SessionBus(private=True)
    yield bus
    bus.close()


# ---------------------------------------------------------------------------
# read_proc_info() -- real /proc reads against a real child process
# ---------------------------------------------------------------------------

def test_read_proc_info_real_child_process_cmdline_and_ppid_chain(mod):
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(5)"],
    )
    try:
        time.sleep(0.3)  # let the child actually get into its sleep
        cmdline, ppid, ppid_cmdline = mod.read_proc_info(proc.pid)
        assert "time.sleep(5)" in cmdline
        assert ppid == str(os.getpid())
        # our own (the pytest process's) cmdline should show up as the parent
        with open(f"/proc/{os.getpid()}/cmdline", "rb") as f:
            our_cmdline = f.read().replace(b"\x00", b" ").decode(errors="replace").strip()
        assert ppid_cmdline == our_cmdline
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_read_proc_info_nonexistent_pid_returns_empty_strings_not_crash(mod):
    # PID 1 is real but /proc/999999999 almost certainly does not exist
    cmdline, ppid, ppid_cmdline = mod.read_proc_info(999999999)
    assert cmdline == ""
    assert ppid == ""
    assert ppid_cmdline == ""


# ---------------------------------------------------------------------------
# log() -- real file writes to a real (throwaway) log path
# ---------------------------------------------------------------------------

def test_log_writes_real_line_with_timestamp_to_real_file(mod, capsys):
    mod.log("[TEST-EVENT] hello world")
    assert os.path.isfile(mod.LOG_PATH)
    with open(mod.LOG_PATH) as f:
        content = f.read()
    assert "[TEST-EVENT] hello world" in content
    assert content.endswith("\n")
    # ISO-ish UTC timestamp prefix, e.g. 2026-08-07T...Z
    assert content.split(" ", 1)[0].endswith("Z")
    printed = capsys.readouterr().out
    assert "[TEST-EVENT] hello world" in printed


def test_log_creates_missing_parent_log_directory(mod):
    mod.LOG_PATH = os.path.join(os.path.dirname(mod.LOG_PATH), "nested", "sub", "audit.log")
    assert not os.path.isdir(os.path.dirname(mod.LOG_PATH))
    mod.log("[TEST] dir created on demand")
    assert os.path.isdir(os.path.dirname(mod.LOG_PATH))
    assert os.path.isfile(mod.LOG_PATH)


def test_log_appends_multiple_real_lines_in_order(mod):
    mod.log("first")
    mod.log("second")
    with open(mod.LOG_PATH) as f:
        lines = [l for l in f.read().splitlines() if l]
    assert len(lines) == 2
    assert lines[0].endswith("first")
    assert lines[1].endswith("second")


# ---------------------------------------------------------------------------
# _handle_message_inner() / handle_message() -- real matching + real resolver
# ---------------------------------------------------------------------------

def test_handle_message_inner_ignores_non_method_call_message_types(mod, real_resolver_bus):
    msg = FakeMessage(type_=4)  # 4 == SIGNAL, not METHOD_CALL
    result = mod._handle_message_inner(msg, real_resolver_bus)
    assert result == dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED


def test_handle_message_inner_ignores_unrelated_interface_and_member(mod, real_resolver_bus):
    msg = FakeMessage(
        type_=1, interface="org.freedesktop.systemd1.Manager", member="ListUnits",
        args=[mod.UNIT],
    )
    result = mod._handle_message_inner(msg, real_resolver_bus)
    assert result == dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED


def test_handle_message_inner_ignores_stopunit_call_for_a_different_unit(mod, real_resolver_bus):
    msg = FakeMessage(
        type_=1, interface="org.freedesktop.systemd1.Manager", member="StopUnit",
        args=["some-other.service"],
    )
    result = mod._handle_message_inner(msg, real_resolver_bus)
    assert result == dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED


def test_handle_message_inner_matches_real_manager_stopunit_and_resolves_real_pid(mod, real_resolver_bus):
    """The sender is our OWN live bus connection's unique name -- a real,
    resolvable D-Bus identity whose PID is verifiably os.getpid()."""
    own_name = real_resolver_bus.get_unique_name()
    msg = FakeMessage(
        type_=1, interface="org.freedesktop.systemd1.Manager", member="StopUnit",
        sender=own_name, args=[mod.UNIT],
    )
    result = mod._handle_message_inner(msg, real_resolver_bus)
    assert result == dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED  # never claims the message

    with open(mod.LOG_PATH) as f:
        content = f.read()
    assert "[STOP-CALL-CAPTURED]" in content
    assert "method=Manager.StopUnit" in content
    assert f"sender={own_name}" in content
    assert f"sender_pid={os.getpid()}" in content
    assert "[STOP-CALL-RAW]" in content


def test_handle_message_inner_matches_unit_interface_stop_member_at_unit_path(mod, real_resolver_bus):
    own_name = real_resolver_bus.get_unique_name()
    msg = FakeMessage(
        type_=1, interface="org.freedesktop.systemd1.Unit", member="Stop",
        path=mod.UNIT_OBJECT_PATH, sender=own_name, args=[],
    )
    mod._handle_message_inner(msg, real_resolver_bus)
    with open(mod.LOG_PATH) as f:
        content = f.read()
    assert "method=Unit.Stop" in content
    assert f"sender_pid={os.getpid()}" in content


def test_handle_message_inner_ignores_unit_interface_at_wrong_object_path(mod, real_resolver_bus):
    msg = FakeMessage(
        type_=1, interface="org.freedesktop.systemd1.Unit", member="Stop",
        path="/org/freedesktop/systemd1/unit/some_2dother_2eservice", args=[],
    )
    result = mod._handle_message_inner(msg, real_resolver_bus)
    assert result == dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED
    assert not os.path.exists(mod.LOG_PATH)


def test_handle_message_inner_unresolvable_sender_logs_unresolved_not_crash(mod, real_resolver_bus):
    """Real DBusException path (GENUINE race the module's own docstring
    documents): a sender name with no live owner on the real bus."""
    msg = FakeMessage(
        type_=1, interface="org.freedesktop.systemd1.Manager", member="KillUnit",
        sender=":1.999999999", args=[mod.UNIT],
    )
    result = mod._handle_message_inner(msg, real_resolver_bus)
    assert result == dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED
    with open(mod.LOG_PATH) as f:
        content = f.read()
    assert "sender_pid=<unresolved:" in content
    assert "method=Manager.KillUnit" in content


def test_handle_message_inner_get_args_list_failure_is_swallowed_treated_as_no_match(mod, real_resolver_bus):
    """message.get_args_list() is wrapped in its own try/except inside
    _handle_message_inner -- a raising get_args_list() must fall back to an
    empty args list (args[0] check then fails safely) rather than
    propagating out of _handle_message_inner itself."""
    msg = FakeMessage(
        type_=1, interface="org.freedesktop.systemd1.Manager", member="StopUnit",
        args_raises=True,
    )
    result = mod._handle_message_inner(msg, real_resolver_bus)
    assert result == dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED
    assert not os.path.exists(mod.LOG_PATH)  # never matched -> never logged


def test_handle_message_outer_wrapper_swallows_exception_and_logs_callback_error(mod, real_resolver_bus):
    """handle_message() is the real outer guard described in the module's
    docstring ("an uncaught exception... risks corrupting the C-level
    connection state"). A message whose get_type() itself raises must be
    caught here, logged, and turned into HANDLER_RESULT_NOT_YET_HANDLED --
    never propagate back into libdbus."""
    msg = FakeMessage(type_raises=True)
    result = mod.handle_message(bus=None, message=msg, resolver=real_resolver_bus)
    assert result == dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED
    with open(mod.LOG_PATH) as f:
        content = f.read()
    assert "[MONITOR-CALLBACK-ERROR]" in content
    assert "RuntimeError" in content
    assert "simulated get_type() failure" in content


def test_handle_message_delegates_successfully_to_inner_for_a_real_match(mod, real_resolver_bus):
    own_name = real_resolver_bus.get_unique_name()
    msg = FakeMessage(
        type_=1, interface="org.freedesktop.systemd1.Manager", member="RestartUnit",
        sender=own_name, args=[mod.UNIT],
    )
    result = mod.handle_message(bus=None, message=msg, resolver=real_resolver_bus)
    assert result == dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED
    with open(mod.LOG_PATH) as f:
        content = f.read()
    assert "method=Manager.RestartUnit" in content
    assert f"sender_pid={os.getpid()}" in content


# ---------------------------------------------------------------------------
# main() -- real wiring: two real bus connections, a real BecomeMonitor call,
# a real message filter registration -- with the blocking main loop's run()
# short-circuited so the test returns instead of hanging forever (Restart=
# always is what keeps this alive in production; a test cannot loop forever).
# ---------------------------------------------------------------------------

def test_main_performs_real_dbus_wiring_and_exits_when_loop_stops(mod, monkeypatch):
    real_run = GLib.MainLoop.run
    calls = {"run": 0}

    def fake_run(self):
        calls["run"] += 1
        # do NOT call real_run -- real_run blocks forever waiting for a
        # GLib event; the real BecomeMonitor call + connection setup above
        # this point already happened for real by the time we get here.

    monkeypatch.setattr(GLib.MainLoop, "run", fake_run)
    try:
        mod.main()
    finally:
        monkeypatch.setattr(GLib.MainLoop, "run", real_run)

    assert calls["run"] == 1
    with open(mod.LOG_PATH) as f:
        content = f.read()
    assert "[MONITOR-START]" in content
    assert f"pid={os.getpid()}" in content
    assert mod.UNIT in content
    assert "[MONITOR-EXIT]" in content


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
