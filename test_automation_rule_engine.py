#!/usr/bin/env python3
"""Real tests for automation_rule_engine.py.

Every test loads the real script-under-test via importlib (spec_from_file_
location + module_from_spec + exec_module, same house convention as
test_apply_owner_dispatch_status_corrections.py), then monkeypatches the
module's own module-level `DB_PATH` global to a real, throwaway temp-file
SQLite database (automation_rule_engine.py hardcodes DB_PATH with no env-var
override, so this is the only real seam available -- per the task's own
instructions, the script itself is never modified). `subprocess.run` is
monkeypatched on the module only at the real dispatch boundary
(_dispatch_action's real external process calls); every real matching/
persistence decision on both sides of that boundary is exercised for real.
"""
import argparse
import importlib.util
import json
import os
import sqlite3
import tempfile

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SUT_PATH = os.path.join(SCRIPTS_DIR, "automation_rule_engine.py")
SBR_PATH = os.path.join(SCRIPTS_DIR, "superboss-register.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_run_ok(stdout_json=None, returncode=0, stderr=""):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        stdout = json.dumps(stdout_json) if stdout_json is not None else ""
        return FakeProc(returncode, stdout, stderr)

    fake_run.calls = calls
    return fake_run


def _refuse_to_run(cmd, **kwargs):
    raise AssertionError(f"subprocess.run must NOT be invoked here, but was called with: {cmd}")


@pytest.fixture
def rule_mod(tmp_path):
    """A real automation_rule_engine.py module instance pointed at a real,
    throwaway temp-file SQLite DB (its own automation_rules/
    automation_rule_runs tables already created via the module's own real
    _ensure_tables())."""
    db_path = str(tmp_path / "scratch.sqlite")
    m = _load(f"automation_rule_engine_scratch_{id(tmp_path)}", SUT_PATH)
    m.DB_PATH = db_path
    conn = sqlite3.connect(db_path)
    m._ensure_tables(conn)
    conn.commit()
    conn.close()
    return m, db_path


def _seed_capability(db_path, capability_name):
    """Seeds a real capability_registry row into the scratch DB, using
    superboss-register.py's own real _ensure_capability_registry_table --
    raw sqlite3 INSERT into a disposable temp DB is the established fixture
    convention (see test_apply_owner_dispatch_status_corrections.py)."""
    bootstrap = _load(f"sbr_bootstrap_are_{id(db_path)}_{capability_name}", SBR_PATH)
    conn = sqlite3.connect(db_path)
    bootstrap._ensure_capability_registry_table(conn)
    conn.execute(
        "INSERT INTO capability_registry (capability_id, ts, capability_name, permissions, owner) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"CAP-{capability_name}", "2026-08-07T00:00:00Z", capability_name, "[]", "test-owner"),
    )
    conn.commit()
    conn.close()


def _ensure_empty_capability_registry(db_path):
    bootstrap = _load(f"sbr_bootstrap_are_empty_{id(db_path)}", SBR_PATH)
    conn = sqlite3.connect(db_path)
    bootstrap._ensure_capability_registry_table(conn)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# _condition_matches -- pure function, no DB
# ---------------------------------------------------------------------------

def test_condition_matches_empty_conditions_always_match():
    m = _load("are_pure_1", SUT_PATH)
    assert m._condition_matches({}, {"anything": 1}) is True
    assert m._condition_matches(None, {}) is True


def test_condition_matches_no_field_key_always_matches():
    m = _load("are_pure_2", SUT_PATH)
    assert m._condition_matches({"operator": "equals"}, {"x": 1}) is True


def test_condition_matches_dotted_field_path_resolution():
    m = _load("are_pure_3", SUT_PATH)
    conditions = {"field": "payload.status", "operator": "equals", "value": "failed"}
    assert m._condition_matches(conditions, {"payload": {"status": "failed"}}) is True
    assert m._condition_matches(conditions, {"payload": {"status": "ok"}}) is False


def test_condition_matches_missing_field_returns_false():
    m = _load("are_pure_4", SUT_PATH)
    conditions = {"field": "a.b.c", "value": "expected"}
    assert m._condition_matches(conditions, {"a": {"b": {}}}) is False
    assert m._condition_matches(conditions, {}) is False


def test_condition_matches_wrong_value_returns_false():
    m = _load("are_pure_5", SUT_PATH)
    conditions = {"field": "event", "value": "invoice.created"}
    assert m._condition_matches(conditions, {"event": "invoice.deleted"}) is False


# ---------------------------------------------------------------------------
# register-rule
# ---------------------------------------------------------------------------

def test_register_rule_without_capability_name_creates_real_row(rule_mod, capsys):
    m, db_path = rule_mod
    parser = m.build_parser()
    args = parser.parse_args([
        "register-rule", "--rule-name", "r-no-cap", "--trigger-type", "umr_status_change",
        "--trigger-conditions", json.dumps({"field": "status", "value": "failed"}),
        "--action-type", "log_action", "--action-config", "{}",
    ])
    args.func(args)
    out = json.loads(capsys.readouterr().out)
    assert out["registered"] is True
    assert out["rule_name"] == "r-no-cap"

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT rule_id, trigger_type, action_type, is_active, capability_name "
        "FROM automation_rules WHERE rule_name = ?", ("r-no-cap",)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == out["rule_id"]
    assert row[1] == "umr_status_change"
    assert row[2] == "log_action"
    assert row[3] == 1
    assert row[4] is None


def test_register_rule_with_valid_capability_name_links_row(rule_mod, capsys):
    m, db_path = rule_mod
    _seed_capability(db_path, "cap-real-1")
    parser = m.build_parser()
    args = parser.parse_args([
        "register-rule", "--rule-name", "r-with-cap", "--trigger-type", "webhook_inbound",
        "--capability-name", "cap-real-1",
        "--action-type", "notify_owner", "--action-config", "{}",
    ])
    args.func(args)
    out = json.loads(capsys.readouterr().out)
    assert out["registered"] is True

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT capability_name FROM automation_rules WHERE rule_name = ?", ("r-with-cap",)
    ).fetchone()
    conn.close()
    assert row[0] == "cap-real-1"


def test_register_rule_with_unregistered_capability_name_fails_cleanly(rule_mod, capsys):
    m, db_path = rule_mod
    _ensure_empty_capability_registry(db_path)
    parser = m.build_parser()
    args = parser.parse_args([
        "register-rule", "--rule-name", "r-bad-cap", "--trigger-type", "webhook_inbound",
        "--capability-name", "does-not-exist",
        "--action-type", "notify_owner", "--action-config", "{}",
    ])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert "does-not-exist" in out["error"]
    assert "capability_registry" in out["error"]

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM automation_rules WHERE rule_name = ?", ("r-bad-cap",)).fetchone()[0]
    conn.close()
    assert count == 0


def test_register_rule_rejects_invalid_trigger_conditions_json(rule_mod, capsys):
    m, db_path = rule_mod
    parser = m.build_parser()
    args = parser.parse_args([
        "register-rule", "--rule-name", "r-bad-json", "--trigger-type", "x",
        "--trigger-conditions", "{not valid json",
        "--action-type", "log_action",
    ])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert "trigger-conditions" in out["error"]


def test_register_rule_rejects_invalid_action_config_json(rule_mod, capsys):
    m, db_path = rule_mod
    parser = m.build_parser()
    args = parser.parse_args([
        "register-rule", "--rule-name", "r-bad-config-json", "--trigger-type", "x",
        "--action-type", "log_action", "--action-config", "{also not json",
    ])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert "action-config" in out["error"]


def test_register_rule_defensive_action_type_check_when_called_directly(rule_mod, capsys):
    """--action-type is normally constrained by argparse `choices`, so this
    internal defensive check is unreachable via the real CLI -- but it is
    real code, reachable if cmd_register_rule is ever called directly
    (e.g. from another script importing this module), so we call it that
    way to exercise the real branch."""
    m, db_path = rule_mod
    ns = argparse.Namespace(
        rule_name="r-bad-action-type", capability_name=None, trigger_type="x",
        trigger_conditions="{}", action_type="totally_not_a_real_action_type",
        action_config="{}",
    )
    with pytest.raises(SystemExit) as exc:
        m.cmd_register_rule(ns)
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert "action-type" in out["error"]


def test_register_rule_upsert_on_conflict_rule_name_updates_existing_row(rule_mod, capsys):
    m, db_path = rule_mod
    parser = m.build_parser()
    first = parser.parse_args([
        "register-rule", "--rule-name", "r-upsert", "--trigger-type", "trigger_a",
        "--action-type", "log_action",
    ])
    first.func(first)
    capsys.readouterr()

    second = parser.parse_args([
        "register-rule", "--rule-name", "r-upsert", "--trigger-type", "trigger_b",
        "--action-type", "notify_owner",
    ])
    second.func(second)
    out2 = json.loads(capsys.readouterr().out)

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT trigger_type, action_type FROM automation_rules WHERE rule_name = ?",
                         ("r-upsert",)).fetchall()
    conn.close()
    assert len(rows) == 1, "unique index on rule_name must prevent a second row"
    assert rows[0][0] == "trigger_b"
    assert rows[0][1] == "notify_owner"
    assert out2["registered"] is True


# ---------------------------------------------------------------------------
# list-rules
# ---------------------------------------------------------------------------

def test_list_rules_filters_by_trigger_type(rule_mod, capsys):
    m, db_path = rule_mod
    parser = m.build_parser()
    for name, trig in [("lr-1", "type_a"), ("lr-2", "type_b"), ("lr-3", "type_a")]:
        a = parser.parse_args([
            "register-rule", "--rule-name", name, "--trigger-type", trig,
            "--trigger-conditions", json.dumps({"field": "x", "value": "y"}),
            "--action-type", "log_action", "--action-config", json.dumps({"content": "c"}),
        ])
        a.func(a)
        capsys.readouterr()

    args = parser.parse_args(["list-rules", "--trigger-type", "type_a"])
    args.func(args)
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 2
    names = sorted(r["rule_name"] for r in out["rules"])
    assert names == ["lr-1", "lr-3"]
    for r in out["rules"]:
        assert isinstance(r["trigger_conditions"], dict)
        assert r["trigger_conditions"] == {"field": "x", "value": "y"}
        assert isinstance(r["action_config"], dict)
        assert r["is_active"] is True

    all_args = parser.parse_args(["list-rules"])
    all_args.func(all_args)
    out_all = json.loads(capsys.readouterr().out)
    assert out_all["count"] == 3


# ---------------------------------------------------------------------------
# evaluate-rules -- real matching + real dispatch-boundary-stubbed side effects
# ---------------------------------------------------------------------------

def _register(parser, capsys, **kwargs):
    defaults = {
        "trigger_conditions": "{}",
        "action_config": "{}",
    }
    defaults.update(kwargs)
    argv = ["register-rule", "--rule-name", defaults["rule_name"],
            "--trigger-type", defaults["trigger_type"],
            "--trigger-conditions", defaults["trigger_conditions"],
            "--action-type", defaults["action_type"],
            "--action-config", defaults["action_config"]]
    args = parser.parse_args(argv)
    args.func(args)
    capsys.readouterr()


def test_evaluate_rules_dispatches_task_gateway_submit_and_records_success_run(rule_mod, capsys, monkeypatch):
    m, db_path = rule_mod
    parser = m.build_parser()
    _register(parser, capsys, rule_name="on-fail-submit", trigger_type="umr_status_change",
              trigger_conditions=json.dumps({"field": "status", "value": "failed"}),
              action_type="task_gateway_submit",
              action_config=json.dumps({"text": "please handle this failure"}))

    fake_run = _fake_run_ok(stdout_json={"submitted": True})
    # NOTE: `subprocess` is a shared, process-wide module singleton -- `m.subprocess`
    # is the exact same object as the real `subprocess` module everywhere else, so a
    # bare `m.subprocess.run = fake_run` would leak into every OTHER module (including
    # backfill_phase_self_report.py's real git/gh calls in the other test file) for
    # the rest of the pytest process. monkeypatch.setattr auto-restores the original
    # after this test, so the stub never leaks across tests/files.
    monkeypatch.setattr(m.subprocess, "run", fake_run)

    args = parser.parse_args([
        "evaluate-rules", "--trigger-type", "umr_status_change",
        "--payload", json.dumps({"status": "failed"}),
    ])
    args.func(args)
    out = json.loads(capsys.readouterr().out)

    assert out["rules_evaluated"] == 1
    assert out["rules_fired"] == 1
    fired = out["fired"][0]
    assert fired["action_type"] == "task_gateway_submit"
    assert fired["status"] == "success"
    assert "run_id" in fired

    assert len(fake_run.calls) == 1
    cmd = fake_run.calls[0]
    assert cmd[0:3] == ["python3", m.TASK_GATEWAY, "submit"]
    assert "please handle this failure" in cmd

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status, trigger_payload, rule_id FROM automation_rule_runs").fetchone()
    conn.close()
    assert row[0] == "success"
    assert json.loads(row[1]) == {"status": "failed"}


def test_evaluate_rules_dispatch_failure_records_failed_run(rule_mod, capsys, monkeypatch):
    m, db_path = rule_mod
    parser = m.build_parser()
    _register(parser, capsys, rule_name="on-fail-fails", trigger_type="umr_status_change",
              trigger_conditions=json.dumps({"field": "status", "value": "failed"}),
              action_type="task_gateway_submit")

    monkeypatch.setattr(m.subprocess, "run", _fake_run_ok(returncode=1, stderr="boom"))

    args = parser.parse_args([
        "evaluate-rules", "--trigger-type", "umr_status_change",
        "--payload", json.dumps({"status": "failed"}),
    ])
    args.func(args)
    out = json.loads(capsys.readouterr().out)
    assert out["rules_fired"] == 1
    assert out["fired"][0]["status"] == "failed"

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status FROM automation_rule_runs").fetchone()
    conn.close()
    assert row[0] == "failed"


def test_evaluate_rules_non_matching_condition_does_not_fire(rule_mod, capsys, monkeypatch):
    m, db_path = rule_mod
    parser = m.build_parser()
    _register(parser, capsys, rule_name="on-fail-nomatch", trigger_type="umr_status_change",
              trigger_conditions=json.dumps({"field": "status", "value": "failed"}),
              action_type="log_action")

    monkeypatch.setattr(m.subprocess, "run", _refuse_to_run)

    args = parser.parse_args([
        "evaluate-rules", "--trigger-type", "umr_status_change",
        "--payload", json.dumps({"status": "running"}),
    ])
    args.func(args)
    out = json.loads(capsys.readouterr().out)
    assert out["rules_evaluated"] == 1
    assert out["rules_fired"] == 0
    assert out["fired"] == []

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM automation_rule_runs").fetchone()[0]
    conn.close()
    assert count == 0


def test_evaluate_rules_dry_run_neither_dispatches_nor_inserts_run_row(rule_mod, capsys, monkeypatch):
    m, db_path = rule_mod
    parser = m.build_parser()
    _register(parser, capsys, rule_name="on-fail-dryrun", trigger_type="umr_status_change",
              trigger_conditions=json.dumps({"field": "status", "value": "failed"}),
              action_type="task_gateway_submit")

    monkeypatch.setattr(m.subprocess, "run", _refuse_to_run)  # must never be called in dry-run mode

    args = parser.parse_args([
        "evaluate-rules", "--trigger-type", "umr_status_change",
        "--payload", json.dumps({"status": "failed"}), "--dry-run",
    ])
    args.func(args)
    out = json.loads(capsys.readouterr().out)
    assert out["rules_fired"] == 1
    fired = out["fired"][0]
    assert fired["dry_run"] is True
    assert fired["would_fire"] is True
    assert "run_id" not in fired
    assert "status" not in fired

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM automation_rule_runs").fetchone()[0]
    conn.close()
    assert count == 0, "dry-run must never insert a real automation_rule_runs row"


def test_evaluate_rules_rejects_invalid_payload_json(rule_mod, capsys):
    m, db_path = rule_mod
    parser = m.build_parser()
    args = parser.parse_args(["evaluate-rules", "--trigger-type", "x", "--payload", "{bad"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert "payload" in out["error"]


def test_evaluate_rules_notify_owner_dispatch(rule_mod, capsys, tmp_path, monkeypatch):
    m, db_path = rule_mod
    fake_notify_owner = tmp_path / "notify-owner.py"
    fake_notify_owner.write_text("#!/usr/bin/env python3\n")
    m.NOTIFY_OWNER = str(fake_notify_owner)

    parser = m.build_parser()
    _register(parser, capsys, rule_name="notify-rule", trigger_type="incident_detected",
              action_type="notify_owner",
              action_config=json.dumps({"subject": "sub", "body": "body text"}))

    fake_run = _fake_run_ok(returncode=0, stdout_json=None)
    monkeypatch.setattr(m.subprocess, "run", fake_run)

    args = parser.parse_args(["evaluate-rules", "--trigger-type", "incident_detected"])
    args.func(args)
    out = json.loads(capsys.readouterr().out)
    assert out["fired"][0]["status"] == "success"
    assert len(fake_run.calls) == 1
    cmd = fake_run.calls[0]
    assert cmd[0:2] == ["python3", str(fake_notify_owner)]
    assert "sub" in cmd
    assert "body text" in cmd


def test_dispatch_action_notify_owner_missing_script_fails_without_subprocess_call(rule_mod, monkeypatch):
    m, db_path = rule_mod
    m.NOTIFY_OWNER = "/nonexistent/path/notify-owner.py"
    monkeypatch.setattr(m.subprocess, "run", _refuse_to_run)
    rule = {"action_type": "notify_owner", "action_config": "{}", "rule_name": "x", "trigger_type": "t"}
    success, result = m._dispatch_action(rule, {})
    assert success is False
    assert "notify-owner.py not found" in result["error"]


def test_evaluate_rules_log_action_dispatch(rule_mod, capsys, monkeypatch):
    m, db_path = rule_mod
    parser = m.build_parser()
    _register(parser, capsys, rule_name="log-rule", trigger_type="anything",
              action_type="log_action",
              action_config=json.dumps({"content": "real content", "term": "real-term"}))

    fake_run = _fake_run_ok(stdout_json={"logged": True})
    monkeypatch.setattr(m.subprocess, "run", fake_run)

    args = parser.parse_args(["evaluate-rules", "--trigger-type", "anything"])
    args.func(args)
    out = json.loads(capsys.readouterr().out)
    assert out["fired"][0]["status"] == "success"
    cmd = fake_run.calls[0]
    assert cmd[0:3] == ["python3", m.SUPERBOSS, "log-action"]
    assert "real content" in cmd
    assert "real-term" in cmd


def test_dispatch_action_unknown_action_type_returns_error_without_subprocess_call(rule_mod, monkeypatch):
    m, db_path = rule_mod
    monkeypatch.setattr(m.subprocess, "run", _refuse_to_run)
    rule = {"action_type": "not_a_real_type", "action_config": "{}", "rule_name": "x", "trigger_type": "t"}
    success, result = m._dispatch_action(rule, {})
    assert success is False
    assert "unknown action_type" in result["error"]


def test_evaluate_rules_only_evaluates_active_rules_of_matching_trigger_type(rule_mod, capsys, monkeypatch):
    m, db_path = rule_mod
    parser = m.build_parser()
    _register(parser, capsys, rule_name="other-trigger", trigger_type="unrelated_trigger",
               action_type="log_action")
    _register(parser, capsys, rule_name="right-trigger", trigger_type="right_type",
               action_type="log_action")

    monkeypatch.setattr(m.subprocess, "run", _fake_run_ok(stdout_json={"ok": True}))
    args = parser.parse_args(["evaluate-rules", "--trigger-type", "right_type"])
    args.func(args)
    out = json.loads(capsys.readouterr().out)
    assert out["rules_evaluated"] == 1
    assert out["fired"][0]["rule_name"] == "right-trigger"
