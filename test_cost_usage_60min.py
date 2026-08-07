#!/usr/bin/env python3
"""Real tests for cost-usage-60min.py.

No production DB is touched by this script at all -- it talks to OpenRouter
over curl (subprocess) and to Postgres over psql (subprocess), both wrapped
by the module's own sh() helper, and it gates on dispatch_core's real
flock/systemctl-backed concurrency check. Per the task's explicit
instruction to stub external subprocess/systemd calls at the boundary:

  - sh() is monkeypatched on the loaded module so check_openrouter_credits()
    and check_token_usage_ledger() run their REAL parsing/aggregation logic
    against controlled, varied stub outputs (never a real curl/psql call).
  - dispatch_core is replaced on the loaded module with a fake object
    exposing the same acquire_dispatch_lock()/has_free_slot() interface, so
    main() never touches the real flock file or shells out to real
    `systemctl --user list-units` (dispatch_core.running_worker_count()'s
    real implementation).

LOG_DIR/JSONL_LOG/TEXT_LOG/ATTENTION_FILE/SHARED_ENV/APP_ENV are all
hardcoded module-level constants (no env-var override), so -- per
convention -- the module is importlib-loaded normally and then those
globals are monkeypatched directly to real temp files/dirs.
"""
import contextlib
import importlib.util
import json
import os
import sys
import types

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SUT_PATH = os.path.join(SCRIPTS_DIR, "cost-usage-60min.py")

# cost-usage-60min.py does `import dispatch_core` at module scope; make sure
# the real dispatch_core.py sitting alongside it is importable regardless of
# how pytest was invoked.
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_dispatch_core(free_slot=True):
    ns = types.SimpleNamespace()

    @contextlib.contextmanager
    def _lock():
        yield

    ns.acquire_dispatch_lock = _lock
    ns.has_free_slot = lambda: free_slot
    return ns


@pytest.fixture
def cu_mod(tmp_path):
    mod = _load("cost_usage_60min_sut", SUT_PATH)
    mod.LOG_DIR = str(tmp_path)
    mod.JSONL_LOG = str(tmp_path / "cost-usage-60min.jsonl")
    mod.TEXT_LOG = str(tmp_path / "cost-usage-60min.log")
    mod.ATTENTION_FILE = str(tmp_path / "ATTENTION.md")
    mod.SHARED_ENV = str(tmp_path / "shared.env")
    mod.APP_ENV = str(tmp_path / "app.env")
    mod.dispatch_core = _fake_dispatch_core(free_slot=True)
    return mod


def make_stub_sh(openrouter=None, ledger=None, default=("", "no stub configured", -1)):
    """Branches on the real command string cost-usage-60min.py builds, the
    same way the real sh() boundary is called from check_openrouter_credits()
    (curl ...) vs check_token_usage_ledger() (psql ...)."""
    def _sh(cmd, timeout=20):
        if "curl" in cmd and openrouter is not None:
            return openrouter
        if "psql" in cmd and ledger is not None:
            return ledger
        return default
    return _sh


# ---------------------------------------------------------------------------
# get_env_value
# ---------------------------------------------------------------------------

def test_get_env_value_strips_quotes_and_finds_real_key(cu_mod, tmp_path):
    p = tmp_path / "env1"
    p.write_text('OPENROUTER_API_KEY="sk-real-key-123"\nOTHER=bareword\n')
    assert cu_mod.get_env_value("OPENROUTER_API_KEY", str(p)) == "sk-real-key-123"
    assert cu_mod.get_env_value("OTHER", str(p)) == "bareword"


def test_get_env_value_missing_key_or_file_returns_none(cu_mod, tmp_path):
    p = tmp_path / "env2"
    p.write_text("SOMETHING_ELSE=1\n")
    assert cu_mod.get_env_value("OPENROUTER_API_KEY", str(p)) is None
    assert cu_mod.get_env_value("OPENROUTER_API_KEY", str(tmp_path / "does-not-exist")) is None


# ---------------------------------------------------------------------------
# check_openrouter_credits
# ---------------------------------------------------------------------------

def test_check_openrouter_credits_no_key_anywhere(cu_mod):
    # SHARED_ENV/APP_ENV point at files that don't exist -> real "not found" path.
    result = cu_mod.check_openrouter_credits()
    assert result == {"available": False, "error": "OPENROUTER_API_KEY not found"}


def test_check_openrouter_credits_success_computes_real_remaining(cu_mod, monkeypatch):
    with open(cu_mod.SHARED_ENV, "w") as f:
        f.write("OPENROUTER_API_KEY=sk-abc123\n")
    payload = json.dumps({"data": {"total_credits": 100.5, "total_usage": 42.125}})
    monkeypatch.setattr(cu_mod, "sh", make_stub_sh(openrouter=(payload, "", 0)))

    result = cu_mod.check_openrouter_credits()
    assert result["available"] is True
    assert result["total_credits"] == 100.5
    assert result["total_usage"] == 42.125
    assert result["remaining_credits"] == 58.375


def test_check_openrouter_credits_curl_failure(cu_mod, monkeypatch):
    with open(cu_mod.SHARED_ENV, "w") as f:
        f.write("OPENROUTER_API_KEY=sk-abc123\n")
    monkeypatch.setattr(cu_mod, "sh", make_stub_sh(openrouter=("", "curl: connection refused", 7)))

    result = cu_mod.check_openrouter_credits()
    assert result["available"] is False
    assert result["error"] == "curl: connection refused"


def test_check_openrouter_credits_unparseable_response(cu_mod, monkeypatch):
    with open(cu_mod.SHARED_ENV, "w") as f:
        f.write("OPENROUTER_API_KEY=sk-abc123\n")
    monkeypatch.setattr(cu_mod, "sh", make_stub_sh(openrouter=("<html>not json</html>", "", 0)))

    result = cu_mod.check_openrouter_credits()
    assert result["available"] is False
    assert "unparseable response" in result["error"]
    assert result["raw"] == "<html>not json</html>"


# ---------------------------------------------------------------------------
# check_token_usage_ledger
# ---------------------------------------------------------------------------

def test_check_token_usage_ledger_no_database_url(cu_mod):
    result = cu_mod.check_token_usage_ledger()
    assert result == {"available": False, "error": "DATABASE_URL not found"}


def test_check_token_usage_ledger_parses_real_rows(cu_mod, monkeypatch):
    with open(cu_mod.APP_ENV, "w") as f:
        f.write("DATABASE_URL=postgres://user:pass@host/db\n")
    psql_out = "glm-4.6,zhipu,12,15000,3000,0.0421\nclaude-sonnet,anthropic,3,900,400,0.015\n"
    monkeypatch.setattr(cu_mod, "sh", make_stub_sh(ledger=(psql_out, "", 0)))

    result = cu_mod.check_token_usage_ledger()
    assert result["available"] is True
    rows = result["rows_last_hour"]
    assert len(rows) == 2
    assert rows[0] == {
        "model": "glm-4.6", "provider": "zhipu", "calls": 12,
        "prompt_tokens": 15000, "completion_tokens": 3000, "cost_usd": 0.0421,
    }
    assert rows[1]["model"] == "claude-sonnet"
    assert rows[1]["cost_usd"] == 0.015


def test_check_token_usage_ledger_zero_rows_still_available(cu_mod, monkeypatch):
    with open(cu_mod.APP_ENV, "w") as f:
        f.write("DATABASE_URL=postgres://user:pass@host/db\n")
    monkeypatch.setattr(cu_mod, "sh", make_stub_sh(ledger=("", "", 0)))

    result = cu_mod.check_token_usage_ledger()
    assert result["available"] is True
    assert result["rows_last_hour"] == []
    assert "AI_TEAM_LOG_SECRET" in result["note"]


def test_check_token_usage_ledger_psql_failure(cu_mod, monkeypatch):
    with open(cu_mod.APP_ENV, "w") as f:
        f.write("DATABASE_URL=postgres://user:pass@host/db\n")
    monkeypatch.setattr(cu_mod, "sh", make_stub_sh(ledger=("", "psql: connection refused", 2)))

    result = cu_mod.check_token_usage_ledger()
    assert result == {"available": False, "error": "psql: connection refused"}


# ---------------------------------------------------------------------------
# check_groq -- real, fixed, honest "not available" contract (not fabricated)
# ---------------------------------------------------------------------------

def test_check_groq_reports_unavailable_honestly(cu_mod):
    result = cu_mod.check_groq()
    assert result["available"] is False
    assert "console.groq.com" in result["note"]


# ---------------------------------------------------------------------------
# rotate
# ---------------------------------------------------------------------------

def test_rotate_trims_to_max_lines(cu_mod, tmp_path):
    p = tmp_path / "rotate_me.log"
    p.write_text("".join(f"line{i}\n" for i in range(10)))
    cu_mod.rotate(str(p), 4)
    remaining = p.read_text().splitlines()
    assert remaining == ["line6", "line7", "line8", "line9"]


def test_rotate_noop_when_under_limit(cu_mod, tmp_path):
    p = tmp_path / "short.log"
    p.write_text("only one line\n")
    cu_mod.rotate(str(p), 168)
    assert p.read_text() == "only one line\n"


def test_rotate_missing_file_does_not_raise(cu_mod, tmp_path):
    cu_mod.rotate(str(tmp_path / "does-not-exist.log"), 10)  # must not raise


# ---------------------------------------------------------------------------
# get_previous_remaining
# ---------------------------------------------------------------------------

def test_get_previous_remaining_reads_last_jsonl_line(cu_mod):
    with open(cu_mod.JSONL_LOG, "w") as f:
        f.write(json.dumps({"openrouter": {"remaining_credits": 10.0}}) + "\n")
        f.write(json.dumps({"openrouter": {"remaining_credits": 7.5}}) + "\n")
    assert cu_mod.get_previous_remaining() == 7.5


def test_get_previous_remaining_no_file_returns_none(cu_mod):
    assert cu_mod.get_previous_remaining() is None


def test_get_previous_remaining_malformed_last_line_returns_none(cu_mod):
    with open(cu_mod.JSONL_LOG, "w") as f:
        f.write("{not valid json\n")
    assert cu_mod.get_previous_remaining() is None


# ---------------------------------------------------------------------------
# main() -- the real entry point, end to end
# ---------------------------------------------------------------------------

def _openrouter_payload(total_credits, total_usage):
    return json.dumps({"data": {"total_credits": total_credits, "total_usage": total_usage}}), "", 0


def test_main_skips_cleanly_when_no_free_slot(cu_mod, capsys):
    cu_mod.dispatch_core = _fake_dispatch_core(free_slot=False)
    cu_mod.main()
    out = capsys.readouterr().out
    assert "SKIP cost-usage-60min (cap reached)" in out
    # Real side effect: nothing should have been written when main() bails
    # out before doing any real cost/usage work.
    assert not os.path.isfile(cu_mod.JSONL_LOG)
    assert not os.path.isfile(cu_mod.TEXT_LOG)


def test_main_success_path_writes_real_jsonl_and_text_log(cu_mod, monkeypatch):
    with open(cu_mod.SHARED_ENV, "w") as f:
        f.write("OPENROUTER_API_KEY=sk-test\n")
    # No DATABASE_URL configured -> ledger genuinely unavailable, a real edge case.
    monkeypatch.setattr(cu_mod, "sh", make_stub_sh(openrouter=_openrouter_payload(50.0, 10.0)))

    cu_mod.main()

    assert os.path.isfile(cu_mod.JSONL_LOG)
    with open(cu_mod.JSONL_LOG) as f:
        record = json.loads(f.readline())
    assert record["openrouter"]["available"] is True
    assert record["openrouter"]["remaining_credits"] == 40.0
    assert record["token_usage_ledger"] == {"available": False, "error": "DATABASE_URL not found"}
    assert record["groq"]["available"] is False
    # First run ever -- no prior JSONL entry, so no spend delta can be computed.
    assert record["openrouter_spend_last_hour_usd"] is None
    assert record["anomalies"] == []

    with open(cu_mod.TEXT_LOG) as f:
        text = f.read()
    assert "OpenRouter remaining=$40.0" in text
    assert "anomalies=0" in text

    # No anomalies -> ATTENTION_FILE must not be created at all.
    assert not os.path.isfile(cu_mod.ATTENTION_FILE)


def test_main_flags_spend_over_threshold_to_attention_file(cu_mod, monkeypatch):
    with open(cu_mod.SHARED_ENV, "w") as f:
        f.write("OPENROUTER_API_KEY=sk-test\n")
    # Prior run left $50 remaining.
    with open(cu_mod.JSONL_LOG, "w") as f:
        f.write(json.dumps({"openrouter": {"remaining_credits": 50.0}}) + "\n")
    # This run: remaining dropped to $45 -> $5.00 spent, above the default
    # $2.0 COST_ALERT_THRESHOLD_USD.
    monkeypatch.setattr(cu_mod, "sh", make_stub_sh(openrouter=_openrouter_payload(100.0, 55.0)))

    cu_mod.main()

    with open(cu_mod.JSONL_LOG) as f:
        lines = f.readlines()
    record = json.loads(lines[-1])
    assert record["openrouter_spend_last_hour_usd"] == 5.0
    assert len(record["anomalies"]) == 1
    assert "exceeds threshold" in record["anomalies"][0]

    assert os.path.isfile(cu_mod.ATTENTION_FILE)
    attention_text = open(cu_mod.ATTENTION_FILE).read()
    assert "cost-usage-60min" in attention_text
    assert "exceeds threshold" in attention_text


def test_main_openrouter_failure_is_recorded_as_anomaly(cu_mod, monkeypatch):
    # No SHARED_ENV/APP_ENV key at all -> real "not found" failure path,
    # which must itself be surfaced as an anomaly (not silently swallowed).
    monkeypatch.setattr(cu_mod, "sh", make_stub_sh())

    cu_mod.main()

    with open(cu_mod.JSONL_LOG) as f:
        record = json.loads(f.readline())
    assert record["openrouter"]["available"] is False
    assert any("OpenRouter credits check failed" in a for a in record["anomalies"])
    assert os.path.isfile(cu_mod.ATTENTION_FILE)


def test_main_rotates_logs_to_max_lines(cu_mod, monkeypatch):
    with open(cu_mod.SHARED_ENV, "w") as f:
        f.write("OPENROUTER_API_KEY=sk-test\n")
    monkeypatch.setattr(cu_mod, "sh", make_stub_sh(openrouter=_openrouter_payload(10.0, 1.0)))
    monkeypatch.setattr(cu_mod, "MAX_LINES", 3)

    # Pre-seed both logs above the (patched) rotation limit.
    with open(cu_mod.JSONL_LOG, "w") as f:
        for i in range(5):
            f.write(json.dumps({"openrouter": {"remaining_credits": float(i)}}) + "\n")
    with open(cu_mod.TEXT_LOG, "w") as f:
        for i in range(5):
            f.write(f"old line {i}\n")

    cu_mod.main()

    with open(cu_mod.JSONL_LOG) as f:
        jsonl_lines = f.readlines()
    with open(cu_mod.TEXT_LOG) as f:
        text_lines = f.readlines()
    # 5 pre-seeded + 1 new = 6, rotated down to the patched MAX_LINES=3.
    assert len(jsonl_lines) == 3
    assert len(text_lines) == 3
    # The newest line (the one main() just appended) must be the one kept.
    last_record = json.loads(jsonl_lines[-1])
    assert last_record["openrouter"]["remaining_credits"] == 9.0
