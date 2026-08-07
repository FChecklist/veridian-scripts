#!/usr/bin/env python3
"""Real tests for generate_chatgpt_promptbatch_request.py.

Loads the target module by file path (importlib) since it lives at the
workspace root and pulls in real sibling modules (chatgpt_promptlib_guard,
generate_prompt_coverage_report, superboss-register.py) via its own
sys.path.insert(0, SCRIPT_DIR) -- exactly as it does in production.

Boundaries actually stubbed (true external boundaries only):
  - The real, live capability registry SQLite DB
    (/opt/veridian/ai-os/memory/superboss-register.sqlite) is NEVER touched.
    Instead SUPERBOSS_REGISTER_DB is pointed at a tmp_path SQLite file,
    bootstrapped via the real `superboss-register.py init` and
    `register-capability` subcommands (never raw SQL).
  - The real, live filesystem write target
    (/opt/veridian/chatgpt-prompt-library/_pending_requests/) is never
    written to. The module-level PENDING_REQUESTS_DIR and the imported
    guarded_write() symbol are monkeypatched to redirect writes into
    tmp_path, but the fake guarded_write still performs a REAL file write
    and we assert on the REAL file's REAL content afterwards.
  - SCHEMA_PATH / VARIABLE_DICTIONARY_PATH point (in production) at
    ai-os/*.yaml files that do not exist in this isolated task workspace;
    they are monkeypatched to real, disk-written tmp_path YAML fixtures
    with the same real shape (csv_schema.columns, placeholder_variable_
    convention, entries) the production files have, per the module's own
    load_schema()/load_variable_dictionary() contract.

Everything else (build_request_text, render_schema_columns,
pick_relevant_entities, compute_coverage/compute_enduser_domain_coverage,
select_diverse_combos, match_capability_for_module, and
load_real_engine_modules against the REAL on-disk compliance-tracker
engines directory) runs as real, unmocked local logic.
"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest
import yaml

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
SUPERBOSS_PATH = os.path.join(WORKSPACE, "superboss-register.py")
TARGET_PATH = os.path.join(WORKSPACE, "generate_chatgpt_promptbatch_request.py")


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def gen_mod():
    """Fresh import of the target module for every test (module-level
    globals like PENDING_REQUESTS_DIR get monkeypatched per-test, so a
    shared cached import would leak state between tests)."""
    return _load_module("gcpr_test_mod", TARGET_PATH)


def _bootstrap_superboss_db(tmp_path, monkeypatch):
    """Real bootstrap of a throwaway SQLite DB via superboss-register.py's
    own real CLI (init), following resolve_superboss_db_path()'s documented
    testability convention: SUPERBOSS_REGISTER_DB is only honored once the
    path already exists and is non-zero size, so we first hand-create a
    minimal valid file containing just umr_tasks (mirroring
    tests/test_resolve_superboss_db_path.py's established pattern) before
    pointing the env var at it and letting the real `init` subcommand build
    out the rest of the real schema (capability_registry, instructions, ...)."""
    import sqlite3

    sbr = _load_module("sbr_bootstrap_mod", SUPERBOSS_PATH)
    db_path = tmp_path / "superboss-test.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    conn.close()
    monkeypatch.setenv("SUPERBOSS_REGISTER_DB", str(db_path))

    proc = subprocess.run(
        ["python3", SUPERBOSS_PATH, "init"], capture_output=True, text=True,
    )
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
    out = json.loads(proc.stdout)
    assert out["capability_name"] == record["capability_name"]
    return out


def _write_real_schema_fixture(tmp_path):
    schema = {
        "csv_schema": {
            "columns": [
                {"name": "Capability", "type": "string", "required": True, "description": "capability_name exact match"},
                {"name": "Prompt", "type": "string", "required": True, "description": "the natural-language prompt"},
                {"name": "Intent", "type": "string", "required": False, "description": "canonical intent label"},
                {
                    "name": "End User Role", "type": "string", "required": False,
                    "description": "who would type this prompt",
                    "values": ["Owner", "Head of Department", "Employee", "Vendor", "Customer"],
                },
                {"name": "Business Domain", "type": "string", "required": False, "description": "which engine domain"},
            ],
        },
        "placeholder_variable_convention": {
            "rule": "Use <Entity.Attribute> tokens for any example data, never a hardcoded literal.",
            "example": "<Clients.Name> owes <Invoices.Amount> as of <Invoices.DueDate>.",
        },
    }
    schema_path = tmp_path / "CHATGPT_PROMPT_SCHEMA_test.yaml"
    schema_path.write_text(yaml.safe_dump(schema), encoding="utf-8")
    return schema, schema_path


def _write_real_variable_dictionary_fixture(tmp_path):
    var_dict = {
        "entries": [
            {"entity": "Users", "placeholder": "<Users.Name>"},
            {"entity": "Users", "placeholder": "<Users.Email>"},
            {"entity": "Clients", "placeholder": "<Clients.Name>"},
            {"entity": "Clients", "placeholder": "<Clients.Id>"},
            {"entity": "Invoices", "placeholder": "<Invoices.Number>"},
            {"entity": "Invoices", "placeholder": "<Invoices.Amount>"},
        ],
    }
    vd_path = tmp_path / "VARIABLE_DICTIONARY_test.yaml"
    vd_path.write_text(yaml.safe_dump(var_dict), encoding="utf-8")
    return var_dict, vd_path


def _fake_guarded_write_factory(calls):
    def _fake_guarded_write(path, content, mode="w", encoding="utf-8"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode, encoding=encoding) as f:
            f.write(content)
        calls.append(path)
        return path
    return _fake_guarded_write


class _Args:
    """Plain stand-in for argparse.Namespace with only the attributes the
    run_* functions actually read."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


# ---------------------------------------------------------------------------
# capability_gap mode: real end-to-end run
# ---------------------------------------------------------------------------

def test_capability_gap_mode_end_to_end_real_request_file(tmp_path, monkeypatch):
    _bootstrap_superboss_db(tmp_path, monkeypatch)
    _register_capability({
        "capability_name": "invoice_status_lookup_test",
        "inputs": ["invoice_id"],
        "business_rules": ["exact invoice_id match"],
        "apis": ["/api/invoices/status"],
        "permissions": "read:invoices",
        "ai_required": False,
        "confidence": 0.9,
        "version": "1.0",
        "owner": "src/lib/engines/accounting-engine.ts",
    })
    _register_capability({
        "capability_name": "vendor_payment_advisory_test",
        "inputs": ["vendor_id"],
        "business_rules": [],
        "apis": [],
        "permissions": "read:vendors",
        "ai_required": True,
        "confidence": 0.4,
        "version": "1.0",
        "owner": "src/lib/engines/procurement-engine.ts",
    })

    mod = _load_module("gcpr_gap_mod", TARGET_PATH)
    _, schema_path = _write_real_schema_fixture(tmp_path)
    _, vd_path = _write_real_variable_dictionary_fixture(tmp_path)
    monkeypatch.setattr(mod, "SCHEMA_PATH", str(schema_path))
    monkeypatch.setattr(mod, "VARIABLE_DICTIONARY_PATH", str(vd_path))

    pending_dir = tmp_path / "pending_requests"
    monkeypatch.setattr(mod, "PENDING_REQUESTS_DIR", str(pending_dir))
    write_calls = []
    monkeypatch.setattr(mod, "guarded_write", _fake_guarded_write_factory(write_calls))

    csv_dir = tmp_path / "csv_empty"
    csv_dir.mkdir()

    args = _Args(count=10, prompts_per_capability=5, csv_dir=str(csv_dir))

    with pytest.raises(SystemExit) as exc:
        mod.run_capability_gap_mode(args)
    assert exc.value.code == 0

    # Real file was actually written, under the redirected (never the live) dir.
    assert len(write_calls) == 1
    written_path = write_calls[0]
    assert written_path.startswith(str(pending_dir))
    assert os.path.exists(written_path)
    content = open(written_path, encoding="utf-8").read()

    # Real content assertions: both real capabilities appear, with the
    # mandatory placeholder rule and hard-rejection instructions present.
    assert "invoice_status_lookup_test" in content
    assert "vendor_payment_advisory_test" in content
    assert "Capability,Prompt,Intent,End User Role,Business Domain" in content
    assert "HARD REJECTION RULE" in content
    assert "<Entity.Attribute>" in content or "Use <Entity.Attribute> tokens" in content
    assert "===== COPY BELOW THIS LINE =====" in content


def test_capability_gap_mode_no_uncovered_capabilities_exits_nonzero(tmp_path, monkeypatch):
    """Real edge/failure case: every capability already has >=1 real prompt
    row -> the script must refuse (exit 1) rather than fabricate a batch."""
    _bootstrap_superboss_db(tmp_path, monkeypatch)
    _register_capability({
        "capability_name": "fully_covered_capability_test",
        "inputs": [], "business_rules": [], "apis": [], "permissions": "read:x",
        "ai_required": False, "confidence": 0.8, "version": "1.0",
        "owner": "src/lib/engines/hr-engine.ts",
    })

    mod = _load_module("gcpr_covered_mod", TARGET_PATH)
    _, schema_path = _write_real_schema_fixture(tmp_path)
    _, vd_path = _write_real_variable_dictionary_fixture(tmp_path)
    monkeypatch.setattr(mod, "SCHEMA_PATH", str(schema_path))
    monkeypatch.setattr(mod, "VARIABLE_DICTIONARY_PATH", str(vd_path))

    csv_dir = tmp_path / "csv_covered"
    csv_dir.mkdir()
    csv_path = csv_dir / "prompts.csv"
    csv_path.write_text(
        "Capability,Prompt,Intent,End User Role,Business Domain\n"
        "fully_covered_capability_test,do the thing,do_thing,Owner,HR & Payroll\n",
        encoding="utf-8",
    )

    args = _Args(count=10, prompts_per_capability=5, csv_dir=str(csv_dir))
    with pytest.raises(SystemExit) as exc:
        mod.run_capability_gap_mode(args)
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Real, disk-verified engine-module loading (no filesystem stubbing --
# this exercises the actual compliance-tracker checkout on this host, which
# is exactly what load_real_engine_modules() is designed to verify against).
# ---------------------------------------------------------------------------

def test_load_real_engine_modules_against_real_checkout(gen_mod):
    modules, dropped = gen_mod.load_real_engine_modules()
    assert isinstance(modules, list)
    # every returned module must genuinely exist on disk right now
    for m in modules:
        real_path = os.path.join(gen_mod.ENGINE_REPO_ROOT, m["relpath"])
        assert os.path.isfile(real_path), f"claimed-real module does not exist: {real_path}"
        assert m["module_slug"] == m["module"][:-3].replace("-", "_")
    # every dropped entry must genuinely NOT exist on disk
    for relpath in dropped:
        real_path = os.path.join(gen_mod.ENGINE_REPO_ROOT, relpath)
        assert not os.path.isfile(real_path)
    # union of found + dropped == every key in the source map, nothing invented
    found_relpaths = {m["relpath"] for m in modules}
    assert found_relpaths | set(dropped) == set(gen_mod.ENGINE_MODULE_DOMAIN_MAP)


# ---------------------------------------------------------------------------
# Pure local-logic unit coverage (real inputs, real outputs)
# ---------------------------------------------------------------------------

def test_match_capability_for_module_real_match_and_fallback(gen_mod):
    module_entry = {"module": "hr-engine.ts", "module_slug": "hr_engine", "relpath": "hr-engine.ts", "domain": "HR & Payroll"}
    capabilities = [
        {"capability_name": "employee_leave_lookup", "owner": "src/lib/engines/hr-engine.ts"},
        {"capability_name": "unrelated_cap", "owner": "src/lib/engines/sales-engine.ts"},
    ]
    matched = gen_mod.match_capability_for_module(module_entry, capabilities)
    assert matched == ["employee_leave_lookup"]

    # No real owner references this module -> documented fallback, not invented.
    no_match = gen_mod.match_capability_for_module(module_entry, [capabilities[1]])
    assert no_match == ["hr_engine_unregistered"]


def test_pick_relevant_entities_substring_and_fallback(gen_mod):
    by_entity = {"Invoices": ["<Invoices.Number>"], "Clients": ["<Clients.Name>"], "Users": ["<Users.Name>"]}
    cap = {"owner": "invoices module", "workflow": "reconcile invoices", "capability_name": "invoice_recon"}
    entities = gen_mod.pick_relevant_entities(cap, by_entity, max_entities=3)
    assert "Invoices" in entities
    # Users/Clients fallback always appended if present and not already matched
    assert "Users" in entities or "Clients" in entities


def test_compute_enduser_domain_coverage_real_counts(gen_mod):
    roles = ["Owner", "Employee"]
    modules = [{"module": "hr-engine.ts", "module_slug": "hr_engine", "relpath": "hr-engine.ts", "domain": "HR & Payroll"}]
    prompts = [
        {"End User Role": "Owner", "Business Domain": "HR & Payroll"},
        {"End User Role": "Owner", "Business Domain": "HR & Payroll"},
    ]
    coverage = gen_mod.compute_enduser_domain_coverage(prompts, roles, modules)
    assert coverage["total_combos"] == 2  # 2 roles x 1 domain
    assert coverage["covered_combos"] == 1
    assert ("Employee", "HR & Payroll") in coverage["missing_combos"]
    assert ("Owner", "HR & Payroll") not in coverage["missing_combos"]


def test_select_diverse_combos_round_robins_across_domains(gen_mod):
    roles = ["Owner", "Employee", "Vendor"]
    modules_by_domain = {
        "Finance & Accounting": [{"module": "accounting-engine.ts"}],
        "HR & Payroll": [{"module": "hr-engine.ts"}],
    }
    missing = [
        ("Owner", "Finance & Accounting"), ("Employee", "Finance & Accounting"),
        ("Owner", "HR & Payroll"), ("Employee", "HR & Payroll"),
    ]
    selected = gen_mod.select_diverse_combos(missing, roles, modules_by_domain, n=4)
    assert len(selected) == 4
    # deliberately not all one domain: both domains must appear
    domains_selected = {d for _, d in selected}
    assert domains_selected == {"Finance & Accounting", "HR & Payroll"}


def test_render_schema_columns_real_shape(gen_mod, tmp_path):
    schema, _ = _write_real_schema_fixture(tmp_path)
    text = gen_mod.render_schema_columns(schema)
    assert "Capability (string, required=True)" in text
    assert "End User Role (string, required=False)" in text
