#!/usr/bin/env python3
"""Real tests for generate-system-diagram.py.

Loaded via importlib.util.spec_from_file_location (hyphenated filename, no
plain `import` possible). No true external boundary exists in this script
(no network/DB/systemd/docker/tmux) -- everything here exercises real,
unmocked local logic, plus real subprocess invocation of the script's own
__main__ entry point with real file writes into tmp_path (never any real
repo path).
"""
import importlib.util
import os
import subprocess
import sys

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
TARGET_PATH = os.path.join(WORKSPACE, "generate-system-diagram.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("generate_system_diagram_test_mod", TARGET_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# render_mermaid(): real structural assertions against the real module data
# ---------------------------------------------------------------------------

def test_render_mermaid_contains_every_real_node_and_edge():
    mod = _load_module()
    mermaid = mod.render_mermaid()

    assert mermaid.startswith("```mermaid\nflowchart TB")
    assert mermaid.rstrip().endswith("```")

    # Every declared node id from both subsystems must appear as a real
    # mermaid node declaration.
    for sub in (mod.SUBSYSTEM_1, mod.SUBSYSTEM_2):
        assert f'subgraph {sub["id"]} ["{sub["title"]}"]' in mermaid
        for node_id, label in sub["nodes"]:
            assert f'{node_id}["{label}"]' in mermaid

    # Every declared edge must appear as a real mermaid arrow.
    for src, dst, label in mod.EDGES:
        assert f'{src} -->|"{label}"| {dst}' in mermaid


def test_render_mermaid_edges_reference_only_declared_nodes():
    """Real structural-integrity check: every edge endpoint must be a real
    node id declared in one of the two subsystems -- a dangling edge would
    silently produce a broken diagram."""
    mod = _load_module()
    all_node_ids = {n for sub in (mod.SUBSYSTEM_1, mod.SUBSYSTEM_2) for n, _ in sub["nodes"]}
    for src, dst, _label in mod.EDGES:
        assert src in all_node_ids, f"edge source {src!r} is not a declared node"
        assert dst in all_node_ids, f"edge dest {dst!r} is not a declared node"


def test_render_mermaid_sanitizes_embedded_double_quotes(monkeypatch):
    """Real edge case: a node/edge label containing a literal double quote
    must be sanitized (replaced with a single quote) so it can't break the
    generated mermaid string literal. Monkeypatch real module-level data
    (not the function itself) with a label carrying an embedded quote, then
    call the real, unmodified render_mermaid()."""
    mod = _load_module()
    fake_sub1 = {
        "id": "sub1",
        "title": mod.SUBSYSTEM_1["title"],
        "nodes": [("s1_quoted", 'a label with an embedded "quote" in it')],
    }
    monkeypatch.setattr(mod, "SUBSYSTEM_1", fake_sub1)
    monkeypatch.setattr(mod, "SUBSYSTEM_2", {"id": "sub2", "title": "empty", "nodes": []})
    monkeypatch.setattr(mod, "EDGES", [("s1_quoted", "s1_quoted", 'edge label with "quotes" too')])

    mermaid = mod.render_mermaid()
    assert "a label with an embedded 'quote' in it" in mermaid
    assert "edge label with 'quotes' too" in mermaid
    # No unescaped internal double-quote survives inside either label.
    node_line = [l for l in mermaid.splitlines() if l.strip().startswith('s1_quoted["')][0]
    assert node_line.count('"') == 2  # only the two label-delimiting quotes remain


# ---------------------------------------------------------------------------
# render_doc(): real content assertions
# ---------------------------------------------------------------------------

def test_render_doc_real_counts_and_structure():
    mod = _load_module()
    doc = mod.render_doc()

    expected_node_count = len(mod.SUBSYSTEM_1["nodes"]) + len(mod.SUBSYSTEM_2["nodes"])
    assert f"{expected_node_count} components" in doc
    assert f"{len(mod.EDGES)} edges" in doc
    assert "# VERIDIAN System Diagram" in doc
    assert "## Two subsystems" in doc
    assert "```mermaid" in doc
    # The rendered mermaid block is really embedded, not just referenced.
    assert mod.render_mermaid() in doc
    assert "Subsystem 1** runs entirely on VERIDIAN-DEV" in doc
    assert "Subsystem 2** is the actual product" in doc


# ---------------------------------------------------------------------------
# __main__ entry point: real subprocess invocation, real file write
# ---------------------------------------------------------------------------

def test_main_writes_real_file_to_explicit_output_path(tmp_path):
    out_path = tmp_path / "SYSTEM_DIAGRAM.md"
    proc = subprocess.run(
        ["python3", TARGET_PATH, str(out_path)], capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr
    assert f"Wrote {out_path}" in proc.stdout
    assert out_path.exists()

    content = out_path.read_text(encoding="utf-8")
    assert "# VERIDIAN System Diagram" in content
    assert "```mermaid" in content
    assert "s1_dispatch" in content
    assert "s2_supabase" in content


def test_main_default_output_path_fails_without_ai_os_dir(tmp_path):
    """Real edge/failure case: with no explicit output arg, the script
    defaults to the relative path 'ai-os/SYSTEM_DIAGRAM.md' and opens it for
    writing with a bare open() call -- it does not create the parent
    directory first. Run from a cwd with no ai-os/ subfolder (as would
    happen if this script is ever invoked from the wrong directory), this
    fails loudly (non-zero exit, FileNotFoundError traceback) rather than
    silently writing somewhere unexpected or creating the directory."""
    empty_dir = tmp_path / "no_ai_os_here"
    empty_dir.mkdir()
    proc = subprocess.run(
        ["python3", TARGET_PATH], capture_output=True, text=True, cwd=str(empty_dir),
    )
    assert proc.returncode != 0
    assert "FileNotFoundError" in proc.stderr
    assert "ai-os/SYSTEM_DIAGRAM.md" in proc.stderr
    assert not (empty_dir / "ai-os").exists()


def test_main_default_output_path_succeeds_when_ai_os_dir_exists(tmp_path):
    """Same default-path behavior, but the honest success case: when the
    expected ai-os/ directory already exists (the real, intended usage from
    a real repo root), the default-path write really does succeed."""
    (tmp_path / "ai-os").mkdir()
    proc = subprocess.run(
        ["python3", TARGET_PATH], capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr
    written = tmp_path / "ai-os" / "SYSTEM_DIAGRAM.md"
    assert written.exists()
    assert "# VERIDIAN System Diagram" in written.read_text(encoding="utf-8")
