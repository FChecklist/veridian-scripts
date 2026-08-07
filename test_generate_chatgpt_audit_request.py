#!/usr/bin/env python3
"""Real tests for generate_chatgpt_audit_request.py -- manual-bridge request
generator for the ChatGPT Audit Workspace.

The SUT (at import time) does:
  - `from chatgpt_audit_guard import ALLOWED_ROOT, guarded_write`
  - `from chatgpt_audit_versioning import VALID_SUBFOLDERS`
  - `import module_gap_audit_lib as mgal`
and computes several module-level path constants (SCHEMA_PATH,
PENDING_REQUESTS_DIR, REPOS_ROOT) from those imports and from its own
__file__ location. None of those constants point anywhere real/safe in this
sandboxed workspace (this file does not live under a real "scripts/"
subdirectory of a real checked-out veridian-ai-os repo, and there is no
/opt/veridian/repos/<name> we're allowed to touch for a throwaway test), so
every test here:
  1. loads a FRESH module instance via importlib file-path loading (after
     evicting chatgpt_audit_guard/chatgpt_audit_versioning/module_gap_audit_lib
     from sys.modules so the SUT's own `from X import Y` triggers a real,
     fresh import we can then patch);
  2. monkeypatches the SUT's own copies of ALLOWED_ROOT-derived paths
     (PENDING_REQUESTS_DIR), SCHEMA_PATH, and REPOS_ROOT to real, throwaway
     tempfile directories -- and chatgpt_audit_guard's own ALLOWED_ROOT
     (which guarded_write's real path check reads at call time) to the same
     throwaway root;
  3. builds a REAL git repository under the throwaway REPOS_ROOT (git init +
     real tracked files + a real commit), so `git ls-files`, `git rev-parse
     HEAD`, etc. all run for real against real on-disk content -- nothing
     about git is stubbed;
  4. writes a REAL minimal schema yaml file that mirrors the real schema's
     documented shape (fields / linkage_fields / domain_subfolders) so
     load_schema()/render_field_contract() exercise real yaml parsing and
     real string building.

Nothing calls any LLM API (this script never does). No real network action.
No writes to any live path outside a tmp_path-scoped throwaway directory.
"""
import glob
import importlib.util
import json
import os
import subprocess
import sys
import uuid

import pytest
import yaml

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SUT_PATH = os.path.join(SCRIPTS_DIR, "generate_chatgpt_audit_request.py")

SCHEMA_DOC = {
    "fields": [
        {"name": "audit_id", "type": "string", "required": True,
         "description": "allocated separately, never supplied by the LLM"},
        {"name": "timestamp", "type": "string", "required": True, "format": "ISO8601"},
        {"name": "risk_score", "type": "integer", "required": True, "range": [0, 100]},
        {"name": "priority", "type": "string", "required": True,
         "enum": ["low", "medium", "high", "critical"]},
        {"name": "confidence_pct", "type": "integer", "required": False},
        {"name": "missing_items", "type": "list", "required": False,
         "description": "required behaviour absent from the audited scope"},
    ],
    "linkage_fields": {"fields": ["related_umr", "related_task", "related_ocid"]},
    "domain_subfolders": [
        "Architecture", "Business", "Capabilities", "Modules", "Metadata", "Database",
        "Rules", "Workflow", "APIs", "UI", "Reports", "Security", "Performance", "AI",
        "Prompts", "Routes", "Testing", "Dependencies", "Integrations", "Observability",
        "Release", "Recommendations", "History",
    ],
}


def _load_fresh(pending_root, schema_path, repos_root):
    for name in ("chatgpt_audit_guard", "chatgpt_audit_versioning", "module_gap_audit_lib"):
        sys.modules.pop(name, None)

    name = f"sut_gen_chatgpt_audit_request_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, SUT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # real `from chatgpt_audit_guard import ...` runs here

    guard_mod = sys.modules["chatgpt_audit_guard"]
    guard_mod.ALLOWED_ROOT = str(pending_root.parent)  # PENDING_REQUESTS_DIR = ALLOWED_ROOT/_pending_requests
    mod.PENDING_REQUESTS_DIR = str(pending_root)
    mod.SCHEMA_PATH = str(schema_path)
    mod.REPOS_ROOT = str(repos_root)
    return mod, guard_mod


def _init_git_repo(repo_path, files):
    os.makedirs(repo_path, exist_ok=True)
    for rel, content in files.items():
        abs_path = os.path.join(repo_path, rel)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
    subprocess.run(["git", "init", "-q"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo_path, check=True)


@pytest.fixture
def env(tmp_path):
    pending_root = tmp_path / "chatgpt-audit" / "_pending_requests"
    schema_path = tmp_path / "SCHEMA.yaml"
    repos_root = tmp_path / "repos"
    with open(schema_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(SCHEMA_DOC, f)

    repo_path = repos_root / "demo-repo"
    _init_git_repo(str(repo_path), {
        "src/lib/engines/security-engine.ts": "export function auditSecurity() { return true; }\n",
        "src/lib/auth-guard.ts": "export function requireAuth() {}\n",
        "PLATFORM_STRATEGY.md": "# Platform strategy overview\n",
        "package.json": '{"name": "demo"}\n',
        "node_modules/junk/index.js": "// should be excluded from discovery\n",
    })

    mod, guard_mod = _load_fresh(pending_root, schema_path, repos_root)
    return mod, guard_mod, str(repo_path), str(pending_root), str(repos_root)


# ---------------------------------------------------------------------------
# discover_files() -- real `git ls-files` + real keyword search
# ---------------------------------------------------------------------------

def test_discover_files_keyword_match_finds_real_security_files(env):
    mod, guard_mod, repo_path, pending_root, repos_root = env
    files, used_fallback = mod.discover_files(repo_path, "Security", max_files=6, explicit_files=None)
    assert used_fallback is False
    assert "src/lib/engines/security-engine.ts" in files
    assert "src/lib/auth-guard.ts" in files
    # excluded dir must never appear even though it would keyword-match nothing here
    assert not any("node_modules" in f for f in files)


def test_discover_files_domain_with_no_keyword_match_falls_back_to_real_overview_docs(env):
    mod, guard_mod, repo_path, pending_root, repos_root = env
    files, used_fallback = mod.discover_files(repo_path, "Performance", max_files=6, explicit_files=None)
    assert used_fallback is True
    assert files == ["PLATFORM_STRATEGY.md"]


def test_discover_files_explicit_files_overrides_and_checks_existence(env):
    mod, guard_mod, repo_path, pending_root, repos_root = env
    files, used_fallback = mod.discover_files(
        repo_path, "Security", max_files=6, explicit_files=["package.json"],
    )
    assert files == ["package.json"]
    assert used_fallback is False


def test_discover_files_explicit_missing_file_raises_filenotfound(env):
    mod, guard_mod, repo_path, pending_root, repos_root = env
    with pytest.raises(FileNotFoundError):
        mod.discover_files(repo_path, "Security", max_files=6, explicit_files=["does/not/exist.ts"])


def test_discover_files_respects_max_files_cap(env):
    mod, guard_mod, repo_path, pending_root, repos_root = env
    files, _ = mod.discover_files(repo_path, "Security", max_files=1, explicit_files=None)
    assert len(files) == 1


# ---------------------------------------------------------------------------
# render_field_contract() / resolve_standards() -- real schema/local-disk logic
# ---------------------------------------------------------------------------

def test_render_field_contract_includes_every_real_field_and_enum_range(env):
    mod, guard_mod, repo_path, pending_root, repos_root = env
    schema = mod.load_schema()
    text = mod.render_field_contract(schema)
    assert "- audit_id (string, required=True)" in text
    assert "enum: ['low', 'medium', 'high', 'critical']" in text
    assert "range: [0, 100]" in text
    assert "related_umr" in text and "related_task" in text


def test_resolve_standards_override_takes_precedence(env):
    mod, guard_mod, repo_path, pending_root, repos_root = env
    result = mod.resolve_standards(repo_path, ["Custom Standard"])
    assert result == ["Custom Standard"]


def test_resolve_standards_falls_back_to_generic_message_when_nothing_found(env):
    mod, guard_mod, repo_path, pending_root, repos_root = env
    # STANDARDS_CANDIDATES holds absolute, real-host paths outside repo_path
    # (e.g. /opt/veridian/ai-os/AI_ENGINEERING_POLICY.yaml) -- this test is
    # about the "nothing found at all" branch, so it forces that branch
    # deterministically rather than depending on whatever happens to exist
    # on this particular host today.
    mod.STANDARDS_CANDIDATES = []
    result = mod.resolve_standards(repo_path, None)
    assert result == ["VERIDIAN AI OS engineering standards (no standards doc found on disk -- Owner should confirm)"]


def test_resolve_standards_finds_real_constitution_doc_in_repo(env):
    mod, guard_mod, repo_path, pending_root, repos_root = env
    with open(os.path.join(repo_path, "VERIDIAN_AI_CONSTITUTION.md"), "w") as f:
        f.write("# constitution\n")
    result = mod.resolve_standards(repo_path, None)
    assert "VERIDIAN_AI_CONSTITUTION.md" in result


# ---------------------------------------------------------------------------
# build_request_text() -- real file content gets embedded, real truncation
# ---------------------------------------------------------------------------

def test_build_request_text_embeds_real_file_content(env):
    mod, guard_mod, repo_path, pending_root, repos_root = env
    schema = mod.load_schema()
    text = mod.build_request_text(
        domain="Security", repo="demo-repo", repo_path=repo_path,
        files_analysed=["src/lib/auth-guard.ts"], used_fallback=False,
        modules_analysed=["lib"], standards_used=["x"], repository_version="main",
        commit_hash="deadbeef", schema=schema, max_file_bytes=20000,
    )
    assert "export function requireAuth() {}" in text
    assert "### repos/demo-repo/src/lib/auth-guard.ts" in text
    assert "DOMAIN (target chatgpt-audit subfolder): Security" in text
    assert "Respond with ONLY the YAML document" in text


def test_build_request_text_truncates_oversized_file_and_notes_it(env):
    mod, guard_mod, repo_path, pending_root, repos_root = env
    big_file = os.path.join(repo_path, "big.ts")
    with open(big_file, "w") as f:
        f.write("x" * 500)
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add big file"], cwd=repo_path, check=True)

    schema = mod.load_schema()
    text = mod.build_request_text(
        domain="Security", repo="demo-repo", repo_path=repo_path,
        files_analysed=["big.ts"], used_fallback=False, modules_analysed=[],
        standards_used=["x"], repository_version="main", commit_hash="deadbeef",
        schema=schema, max_file_bytes=100,
    )
    assert "truncated at 100 bytes" in text
    # only the first 100 real bytes should be present verbatim
    assert "x" * 100 in text
    assert "x" * 101 not in text


def test_build_request_text_used_fallback_adds_disclaimer_note(env):
    mod, guard_mod, repo_path, pending_root, repos_root = env
    schema = mod.load_schema()
    text = mod.build_request_text(
        domain="Performance", repo="demo-repo", repo_path=repo_path,
        files_analysed=["PLATFORM_STRATEGY.md"], used_fallback=True, modules_analysed=[],
        standards_used=["x"], repository_version="main", commit_hash="deadbeef",
        schema=schema, max_file_bytes=20000,
    )
    assert "starter/partial scope, not a full domain audit" in text


# ---------------------------------------------------------------------------
# main() -- real CLI, real git, real guarded_write side effect
# ---------------------------------------------------------------------------

def test_main_domain_mode_writes_real_request_file_and_prints_ok_json(env, monkeypatch, capsys):
    mod, guard_mod, repo_path, pending_root, repos_root = env
    monkeypatch.setattr(sys, "argv", [
        "generate_chatgpt_audit_request.py", "--domain", "Security", "--repo", "demo-repo",
    ])
    with pytest.raises(SystemExit) as exc_info:
        mod.main()
    assert exc_info.value.code == 0

    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["mode"] == "domain"
    assert out["domain"] == "Security"
    assert "src/lib/auth-guard.ts" in out["files_analysed"]
    assert len(out["commit_hash"]) == 40  # real git rev-parse HEAD

    written = glob.glob(os.path.join(pending_root, "Security-request-*.txt"))
    assert len(written) == 1
    with open(written[0], encoding="utf-8") as f:
        content = f.read()
    assert "===== COPY BELOW THIS LINE =====" in content
    assert "export function requireAuth() {}" in content
    assert out["request_path"] == written[0]


def test_main_domain_mode_repo_not_found_exits_1_and_writes_nothing(env, monkeypatch, capsys):
    mod, guard_mod, repo_path, pending_root, repos_root = env
    monkeypatch.setattr(sys, "argv", [
        "generate_chatgpt_audit_request.py", "--domain", "Security", "--repo", "does-not-exist",
    ])
    with pytest.raises(SystemExit) as exc_info:
        mod.main()
    assert exc_info.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "repo not found" in out["error"]
    assert not os.path.isdir(pending_root) or not os.listdir(pending_root)


def test_main_module_mode_projexa_writes_real_request_file(env, monkeypatch, capsys):
    """Real integration across generate_chatgpt_audit_request.py and
    module_gap_audit_lib.py for the projexa (directory-structure) module
    boundary source -- no compliance-tracker/drizzle scaffolding needed."""
    mod, guard_mod, repo_path, pending_root, repos_root = env
    projexa_path = os.path.join(repos_root, "projexa")
    _init_git_repo(projexa_path, {
        "src/app/api/board_meetings/route.ts": "export async function GET() { return new Response('ok'); }\n",
        "PLATFORM_STRATEGY.md": "# overview\n",
    })

    monkeypatch.setattr(sys, "argv", [
        "generate_chatgpt_audit_request.py", "--module", "board_meetings", "--repo", "projexa",
    ])
    with pytest.raises(SystemExit) as exc_info:
        mod.main()
    assert exc_info.value.code == 0

    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["mode"] == "module"
    assert out["module"] == "board_meetings"
    assert "src/app/api/board_meetings/route.ts" in out["files_analysed"]

    written = glob.glob(os.path.join(pending_root, "Module-projexa-board_meetings-request-*.txt"))
    assert len(written) == 1
    with open(written[0], encoding="utf-8") as f:
        content = f.read()
    assert "export async function GET()" in content


def test_main_module_mode_unknown_module_exits_1_with_available_modules_sample(env, monkeypatch, capsys):
    mod, guard_mod, repo_path, pending_root, repos_root = env
    projexa_path = os.path.join(repos_root, "projexa")
    _init_git_repo(projexa_path, {
        "src/app/api/board_meetings/route.ts": "export async function GET() {}\n",
    })
    monkeypatch.setattr(sys, "argv", [
        "generate_chatgpt_audit_request.py", "--module", "totally-made-up-module", "--repo", "projexa",
    ])
    with pytest.raises(SystemExit) as exc_info:
        mod.main()
    assert exc_info.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "not found" in out["error"]
    assert "board_meetings" in out["available_modules_sample"]


def test_main_no_domain_no_module_exits_2_argparse_error(env, monkeypatch, capsys):
    mod, guard_mod, repo_path, pending_root, repos_root = env
    monkeypatch.setattr(sys, "argv", ["generate_chatgpt_audit_request.py", "--repo", "demo-repo"])
    with pytest.raises(SystemExit) as exc_info:
        mod.main()
    assert exc_info.value.code == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
