#!/usr/bin/env python3
"""Real tests for decision-service.py.

Loads the target (hyphenated filename, so no plain `import`) via
importlib.util.spec_from_file_location, same convention as
tests/test_resolve_superboss_db_path.py. Exercises the REAL
policy_decision.py functions (classify_risk_tier/emit_allow/emit_deny/
make_explanation) it imports -- nothing about those is mocked.

Boundaries actually stubbed (true external boundaries only):
  - risk-tier: a REAL temporary git repository is created under tmp_path
    and real commits are made -- `git` itself is the real subprocess, not
    stubbed at all.
  - capability-check: the live, production
    /opt/veridian/ai-os/memory/superboss-register.sqlite is NEVER opened.
    SUPERBOSS_REGISTER_DB is redirected to a tmp_path SQLite file,
    bootstrapped through superboss-register.py's own real `init` and
    `register-capability` CLI subcommands (never raw SQL) -- the real
    decision-service.py subprocess call to superboss-register.py
    `lookup-capability` inherits this env var and truly runs against the
    tmp DB.
"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
SUPERBOSS_PATH = os.path.join(WORKSPACE, "superboss-register.py")
TARGET_PATH = os.path.join(WORKSPACE, "decision-service.py")


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ds():
    return _load_module("decision_service_test_mod", TARGET_PATH)


def _run_git(args, cwd):
    proc = subprocess.run(["git", "-C", str(cwd)] + args, capture_output=True, text=True)
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout


def _make_git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(["init", "-q"], repo)
    _run_git(["config", "user.email", "test@example.com"], repo)
    _run_git(["config", "user.name", "Test"], repo)
    return repo


# ---------------------------------------------------------------------------
# risk-tier subcommand: real git repo, real diff, real classification
# ---------------------------------------------------------------------------

def test_risk_tier_tier1_allow_on_ordinary_file_change(ds, tmp_path, capsys):
    repo = _make_git_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _run_git(["add", "."], repo)
    _run_git(["commit", "-q", "-m", "base"], repo)
    _run_git(["tag", "base"], repo)

    (repo / "src" / "app.py").write_text("print('v1')\nprint('v2')\n", encoding="utf-8")
    _run_git(["add", "."], repo)
    _run_git(["commit", "-q", "-m", "small change"], repo)

    rc = ds.cmd_risk_tier(str(repo), "base")
    assert rc == 0

    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "allow"
    assert out["risk_tier"] == "tier1"
    assert out["reason_code"] == "tier1_autonomous"
    assert out["source_gate"] == "decision-service.py:risk-tier"
    assert out["evidence"] == []
    assert "autonomous merge" in out["explanation"]["summary"]


def test_risk_tier_tier2_review_on_migrations_path(ds, tmp_path, capsys):
    repo = _make_git_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _run_git(["add", "."], repo)
    _run_git(["commit", "-q", "-m", "base"], repo)
    _run_git(["tag", "base"], repo)

    (repo / "migrations").mkdir()
    (repo / "migrations" / "0001_init.sql").write_text("CREATE TABLE t (id INTEGER);\n", encoding="utf-8")
    _run_git(["add", "."], repo)
    _run_git(["commit", "-q", "-m", "add migration"], repo)

    rc = ds.cmd_risk_tier(str(repo), "base")
    assert rc == 0

    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "review"
    assert out["risk_tier"] == "tier2"
    assert out["reason_code"] == "tier2_requires_signoff"
    assert any("migrations" in r for r in out["evidence"])
    assert "holds for human sign-off" in out["explanation"]["summary"]


def test_risk_tier_tier2_review_on_heavy_deletion(ds, tmp_path, capsys):
    """Real edge case: no tier2 path pattern matched at all, but the heavy-
    deletion heuristic (total_del > 20 and total_del > 2x total_add) alone
    must still force tier2."""
    repo = _make_git_repo(tmp_path)
    original_lines = "\n".join(f"unique_line_{i}" for i in range(40)) + "\n"
    (repo / "notes.txt").write_text(original_lines, encoding="utf-8")
    _run_git(["add", "."], repo)
    _run_git(["commit", "-q", "-m", "base"], repo)
    _run_git(["tag", "base"], repo)

    (repo / "notes.txt").write_text("only_one_new_line\n", encoding="utf-8")
    _run_git(["add", "."], repo)
    _run_git(["commit", "-q", "-m", "gut the file"], repo)

    numstat = _run_git(["diff", "--numstat", "base...HEAD"], repo).splitlines()
    added, deleted = (int(x) for x in numstat[0].split("\t")[:2])
    assert deleted > 20 and deleted > added * 2, f"fixture did not produce a heavy deletion: +{added}/-{deleted}"

    rc = ds.cmd_risk_tier(str(repo), "base")
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "review"
    assert out["risk_tier"] == "tier2"
    assert any("heavy deletion" in r for r in out["evidence"])


# ---------------------------------------------------------------------------
# capability-check subcommand: real subprocess call to superboss-register.py
# against a real, isolated tmp SQLite DB (never the live one)
# ---------------------------------------------------------------------------

def _bootstrap_superboss_db(tmp_path, monkeypatch):
    import sqlite3

    sbr = _load_module("sbr_bootstrap_mod_ds", SUPERBOSS_PATH)
    db_path = tmp_path / "superboss-test.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    conn.close()
    monkeypatch.setenv("SUPERBOSS_REGISTER_DB", str(db_path))

    proc = subprocess.run(["python3", SUPERBOSS_PATH, "init"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return db_path


def _register_capability(record):
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(record, f)
        record_file = f.name
    proc = subprocess.run(
        ["python3", SUPERBOSS_PATH, "register-capability", "--record-file", record_file],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_capability_check_deterministic_match_emits_deny(ds, tmp_path, monkeypatch, capsys):
    _bootstrap_superboss_db(tmp_path, monkeypatch)
    _register_capability({
        "capability_name": "invoicestatuslookupzzq",
        "inputs": ["invoice_id"],
        "business_rules": ["exact invoice_id match"],
        "apis": ["/api/invoices/status"],
        "permissions": "read:invoices",
        "ai_required": False,
        "confidence": 0.95,
        "version": "1.0",
        "owner": "src/lib/engines/accounting-engine.ts",
    })

    rc = ds.cmd_capability_check("invoicestatuslookupzzq")
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "deny"
    assert out["reason_code"] == "deterministic_capability_available"
    assert out["capability_match"]["capability_name"] == "invoicestatuslookupzzq"
    assert out["capability_match"]["ai_required"] is False
    assert out["capability_match"]["business_rules_registered"] is True


def test_capability_check_ai_required_match_emits_allow(ds, tmp_path, monkeypatch, capsys):
    _bootstrap_superboss_db(tmp_path, monkeypatch)
    _register_capability({
        "capability_name": "freeformadvisorychatzzq",
        "inputs": [], "business_rules": [], "apis": [], "permissions": "read:advisory",
        "ai_required": True, "confidence": 0.4, "version": "1.0",
        "owner": "src/lib/engines/procurement-engine.ts",
    })

    rc = ds.cmd_capability_check("freeformadvisorychatzzq")
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "allow"
    assert out["reason_code"] == "ai_call_permitted"
    assert out["capability_match"] is None
    assert "requires AI" in out["explanation"]["summary"]


def test_capability_check_no_match_emits_allow(ds, tmp_path, monkeypatch, capsys):
    _bootstrap_superboss_db(tmp_path, monkeypatch)
    rc = ds.cmd_capability_check("zzzznonexistentcapabilityqqq999")
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "allow"
    assert out["reason_code"] == "ai_call_permitted"
    assert "No deterministic capability match found" in out["explanation"]["summary"]


# ---------------------------------------------------------------------------
# main() CLI-level argument validation edge cases (real subprocess, no DB
# access needed since these fail before ever reaching a subcommand handler)
# ---------------------------------------------------------------------------

def test_main_cli_missing_args_exits_nonzero():
    proc = subprocess.run(["python3", TARGET_PATH], capture_output=True, text=True)
    assert proc.returncode == 1
    out = json.loads(proc.stdout)
    assert "usage" in out["error"]


def test_main_cli_unknown_subcommand_exits_nonzero():
    proc = subprocess.run(["python3", TARGET_PATH, "not-a-real-subcommand"], capture_output=True, text=True)
    assert proc.returncode == 1
    out = json.loads(proc.stdout)
    assert "unknown subcommand" in out["error"]


def test_main_cli_risk_tier_wrong_arg_count_exits_nonzero():
    proc = subprocess.run(["python3", TARGET_PATH, "risk-tier", "only-one-arg"], capture_output=True, text=True)
    assert proc.returncode == 1
    out = json.loads(proc.stdout)
    assert "usage" in out["error"]
