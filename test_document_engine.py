"""Real tests for document_engine.py.

Every test either (a) calls the script's real functions in-process, (b)
invokes the script as a real subprocess and inspects real stdout/stderr/
exit code, or (c) drives its 'register-capabilities' subcommand against a
real, isolated, temp-file SQLite database seeded with the real schema via
superboss-register.py's own bootstrap functions -- never the live
production database at /opt/veridian/ai-os/memory/superboss-register.sqlite
or /opt/veridian/scripts/superboss-register.sqlite.

The one true external boundary this script has -- its cross-call into
automation_rule_engine.py (which itself hard-codes the LIVE
/opt/veridian/ai-os/memory/superboss-register.sqlite with no env override)
-- is stubbed with a fake `python3` executable placed first on PATH that
intercepts only calls targeting automation_rule_engine.py, logs the real
argv it was given, and returns a controlled response. The real
detect-duplicates logic and the real register-capabilities flow against
superboss-register.py are never stubbed.
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent
SCRIPT = REPO_ROOT / "document_engine.py"
SBR_PATH = REPO_ROOT / "superboss-register.py"

sys.path.insert(0, str(REPO_ROOT))
import document_engine as de  # noqa: E402


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_scratch_db(db_path: str):
    """Creates a real, minimal-but-valid scratch SQLite DB (umr_tasks table
    required by superboss-register.py's own resolve_superboss_db_path()
    check), using its real, real bootstrap helper -- never raw ad hoc DDL."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    bootstrap = _load_module("sbr_bootstrap_de", str(SBR_PATH))
    bootstrap._ensure_umr_table(conn)
    conn.commit()
    conn.close()


def _run(args, env=None, cwd=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, timeout=30, env=env, cwd=cwd,
    )


# ---------------------------------------------------------------------------
# detect_duplicate_documents_by_hash -- real function, direct calls
# ---------------------------------------------------------------------------

def test_detect_duplicate_documents_by_hash_finds_real_duplicate_group():
    documents = [
        {"id": "doc-1", "contentHash": "aaa"},
        {"id": "doc-2", "contentHash": "bbb"},
        {"id": "doc-3", "contentHash": "aaa"},
    ]
    groups = de.detect_duplicate_documents_by_hash(documents)
    assert groups == [["doc-1", "doc-3"]]


def test_detect_duplicate_documents_by_hash_no_duplicates_returns_empty():
    documents = [
        {"id": "doc-1", "contentHash": "aaa"},
        {"id": "doc-2", "contentHash": "bbb"},
    ]
    assert de.detect_duplicate_documents_by_hash(documents) == []


def test_detect_duplicate_documents_by_hash_multiple_groups_and_triple():
    documents = [
        {"id": "a", "contentHash": "h1"},
        {"id": "b", "contentHash": "h2"},
        {"id": "c", "contentHash": "h1"},
        {"id": "d", "contentHash": "h2"},
        {"id": "e", "contentHash": "h1"},
    ]
    groups = de.detect_duplicate_documents_by_hash(documents)
    assert sorted(tuple(g) for g in groups) == sorted([("a", "c", "e"), ("b", "d")])


# ---------------------------------------------------------------------------
# detect-duplicates CLI subcommand -- real subprocess
# ---------------------------------------------------------------------------

def test_cli_detect_duplicates_real_subprocess_json_output():
    documents = [
        {"id": "x1", "contentHash": "hash-a"},
        {"id": "x2", "contentHash": "hash-b"},
        {"id": "x3", "contentHash": "hash-a"},
    ]
    result = _run(["detect-duplicates", "--documents", json.dumps(documents)])
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["input_count"] == 3
    assert payload["duplicate_groups"] == [["x1", "x3"]]


def test_cli_detect_duplicates_invalid_json_exits_nonzero_with_error():
    result = _run(["detect-duplicates", "--documents", "{not valid json"])
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert "error" in payload
    assert "must be valid JSON" in payload["error"]


# ---------------------------------------------------------------------------
# register-capabilities -- real subprocess, real superboss-register.py CLI,
# real temp-file SQLite DB (never the live one)
# ---------------------------------------------------------------------------

def test_register_capabilities_real_run_against_temp_db(tmp_path):
    db_path = str(tmp_path / "scratch_superboss.sqlite")
    _seed_scratch_db(db_path)

    env = dict(os.environ)
    env["SUPERBOSS_REGISTER_DB"] = db_path

    result = _run(["register-capabilities"], env=env)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

    payload = json.loads(result.stdout)
    assert payload["schema_rows_found"] == 5
    assert payload["registered_count"] == 5
    assert payload["failed_count"] == 0
    assert payload["failed"] == []

    # Real side-effect verification: read (not write) the scratch DB directly
    # to confirm the 5 real capability rows actually landed, with real field
    # values -- not just that the CLI printed a success count.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT capability_name, ai_required, confidence FROM capability_registry ORDER BY capability_name"
    ).fetchall()
    conn.close()

    names = [r["capability_name"] for r in rows]
    assert names == sorted(cap["capability_name"] for cap in de.DOCUMENT_ENGINE_CAPABILITIES)

    dup_row = next(r for r in rows if r["capability_name"] == "document_duplicate_detection")
    assert dup_row["ai_required"] == 0
    assert dup_row["confidence"] == 1.0


def test_register_capabilities_is_idempotent_reregistration_updates_not_duplicates(tmp_path):
    db_path = str(tmp_path / "scratch_superboss2.sqlite")
    _seed_scratch_db(db_path)
    env = dict(os.environ)
    env["SUPERBOSS_REGISTER_DB"] = db_path

    first = _run(["register-capabilities"], env=env)
    assert first.returncode == 0
    second = _run(["register-capabilities"], env=env)
    assert second.returncode == 0

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM capability_registry").fetchone()[0]
    conn.close()
    # capability_name is UNIQUE and register-capability upserts -- running
    # register-capabilities twice must still leave exactly 5 rows, not 10.
    assert count == 5


# ---------------------------------------------------------------------------
# report-failure -- real subprocess, real payload construction, with the
# automation_rule_engine.py cross-call (a true external boundary: it
# hard-codes the LIVE production DB with no override) stubbed via a fake
# `python3` placed first on PATH.
# ---------------------------------------------------------------------------

FAKE_PY3_TEMPLATE = """\
import json
import os
import sys

log_file = os.environ["FAKE_PY3_LOG"]
with open(log_file, "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")

if len(sys.argv) >= 2 and sys.argv[1].endswith("automation_rule_engine.py"):
    exit_code = int(os.environ.get("FAKE_RULE_ENGINE_EXIT", "0"))
    sys.stdout.write(os.environ.get("FAKE_RULE_ENGINE_STDOUT", "{}"))
    sys.stderr.write(os.environ.get("FAKE_RULE_ENGINE_STDERR", ""))
    sys.exit(exit_code)

sys.exit(0)
"""


def _write_fake_python3(bin_dir: Path) -> Path:
    stub = bin_dir / "python3"
    stub.write_text(f"#!{sys.executable}\n" + FAKE_PY3_TEMPLATE)
    stub.chmod(0o755)
    return stub


def test_report_failure_success_path_builds_real_payload_and_passes_through_stdout(tmp_path):
    log_file = tmp_path / "fake_py3.log"
    _write_fake_python3(tmp_path)

    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["FAKE_PY3_LOG"] = str(log_file)
    env["FAKE_RULE_ENGINE_EXIT"] = "0"
    env["FAKE_RULE_ENGINE_STDOUT"] = json.dumps({"matched_rules": 1, "actions_dispatched": 1})

    result = _run(
        ["report-failure", "--operation", "document_ocr_paddleocr",
         "--reason", "PaddleOCR service timed out", "--job-id", "job-42"],
        env=env,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert json.loads(result.stdout.strip()) == {"matched_rules": 1, "actions_dispatched": 1}

    calls = [json.loads(line) for line in log_file.read_text().splitlines()]
    assert len(calls) == 1
    call_argv = calls[0]
    assert call_argv[0].endswith("automation_rule_engine.py")
    assert call_argv[1] == "evaluate-rules"
    assert "--trigger-type" in call_argv
    assert call_argv[call_argv.index("--trigger-type") + 1] == "document.pipeline_failed"
    payload = json.loads(call_argv[call_argv.index("--payload") + 1])
    assert payload == {
        "operation": "document_ocr_paddleocr",
        "reason": "PaddleOCR service timed out",
        "job_id": "job-42",
    }


def test_report_failure_propagates_failure_from_automation_rule_engine(tmp_path):
    log_file = tmp_path / "fake_py3.log"
    _write_fake_python3(tmp_path)

    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["FAKE_PY3_LOG"] = str(log_file)
    env["FAKE_RULE_ENGINE_EXIT"] = "1"
    env["FAKE_RULE_ENGINE_STDERR"] = "boom: no rule table"

    result = _run(
        ["report-failure", "--operation", "document_pdf_generation",
         "--reason", "header render crashed"],
        env=env,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error"] == "automation_rule_engine.py evaluate-rules failed"
    assert "boom: no rule table" in payload["stderr"]


def test_report_failure_job_id_defaults_to_null_when_omitted(tmp_path):
    log_file = tmp_path / "fake_py3.log"
    _write_fake_python3(tmp_path)

    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["FAKE_PY3_LOG"] = str(log_file)
    env["FAKE_RULE_ENGINE_EXIT"] = "0"
    env["FAKE_RULE_ENGINE_STDOUT"] = "{}"

    result = _run(
        ["report-failure", "--operation", "document_extraction_text", "--reason", "vision call failed"],
        env=env,
    )
    assert result.returncode == 0
    calls = [json.loads(line) for line in log_file.read_text().splitlines()]
    payload = json.loads(calls[0][calls[0].index("--payload") + 1])
    assert payload["job_id"] is None
