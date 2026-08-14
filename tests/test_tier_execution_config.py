#!/usr/bin/env python3
"""task-20260814-135001 / UMR-20260814-131248-baed (real fix for PR #374's
AUDIT:FAIL, superseding task-20260814-131322's original aider-chat+litellm+
OpenRouter/GLM-5.2 tier-3/4 design): tier_execution_config.json is the one
real, single-source-of-truth tier -> execution-backend config
(task-gateway.py's tier_execution_settings()/execution_path_for_tier(), and
dispatch-owner-task.sh's own local fallback both read it). Covers:
  1. the real config file's own shape/values for every tier.
  2. task-gateway.py's config-driven lookup functions against that real
     file (not a mock -- this is the actual file dispatch-owner-task.sh
     ships with).
  3. structural regression guards on dispatch-owner-task.sh: the tier-3/4
     branch must invoke `claude`/`timeout`/the cleanup trap, and must never
     re-introduce a real aider/litellm/OpenRouter/GLM-5.2 execution call
     (comments referencing the dropped design by name are fine; an
     executable invocation is not).
"""
import importlib.util
import json
import os
import re

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(SCRIPTS_DIR, "tier_execution_config.json")
DISPATCH_SH = os.path.join(SCRIPTS_DIR, "dispatch-owner-task.sh")


def _load_task_gateway():
    spec = importlib.util.spec_from_file_location(
        "tg_tier_exec_test", os.path.join(SCRIPTS_DIR, "task-gateway.py"))
    tg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tg)
    return tg


def test_config_file_exists_and_is_valid_json():
    assert os.path.isfile(CONFIG_PATH)
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    assert cfg["default_execution_path"] == "claude_code_cli"
    assert set(cfg["tiers"]) == {"0", "1", "2", "3", "4"}


def test_tier_0_1_2_unchanged_interactive_path():
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    for tier in ("0", "1", "2"):
        assert cfg["tiers"][tier]["execution_path"] == "claude_code_cli"
        # tier 0-2 take no settings from this file (unchanged tmux-relay path)
        assert "model" not in cfg["tiers"][tier]
        assert "timeout_seconds" not in cfg["tiers"][tier]


def test_tier_3_4_headless_claude_code_cli_with_real_settings():
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    for tier in ("3", "4"):
        tier_cfg = cfg["tiers"][tier]
        assert tier_cfg["execution_path"] == "claude_code_cli_headless"
        assert isinstance(tier_cfg["timeout_seconds"], int) and tier_cfg["timeout_seconds"] > 0
        assert isinstance(tier_cfg["max_budget_usd"], (int, float)) and tier_cfg["max_budget_usd"] > 0
        assert tier_cfg["effort"] in ("low", "medium", "high", "xhigh", "max")
        # real model id/alias, never an OpenRouter/GLM slug
        model = tier_cfg["model"].lower()
        assert "openrouter" not in model
        assert "glm" not in model
        assert "z-ai" not in model


def test_no_execution_path_anywhere_in_config_references_a_non_claude_backend():
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    for tier_cfg in cfg["tiers"].values():
        assert tier_cfg["execution_path"].startswith("claude_code_cli")
    assert cfg["default_execution_path"].startswith("claude_code_cli")


def test_task_gateway_execution_path_for_tier_matches_config_file():
    tg = _load_task_gateway()
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    assert tg.execution_path_for_tier(None) is None
    for tier in range(5):
        assert tg.execution_path_for_tier(tier) == cfg["tiers"][str(tier)]["execution_path"]


def test_task_gateway_tier_execution_settings_full_dict():
    tg = _load_task_gateway()
    settings = tg.tier_execution_settings(3)
    assert settings["execution_path"] == "claude_code_cli_headless"
    assert "model" in settings and "timeout_seconds" in settings and "max_budget_usd" in settings
    assert "_note" not in settings  # internal doc field never leaks into a caller's contract

    settings_none = tg.tier_execution_settings(None)
    assert settings_none is None

    settings_tier0 = tg.tier_execution_settings(0)
    assert settings_tier0 == {"execution_path": "claude_code_cli"}


def test_task_gateway_falls_back_safely_on_missing_config(tmp_path, monkeypatch):
    tg = _load_task_gateway()
    monkeypatch.setattr(tg, "TIER_EXECUTION_CONFIG_PATH", str(tmp_path / "does-not-exist.json"))
    # fail-closed default must still only ever produce a claude_code_cli* path
    assert tg.execution_path_for_tier(2) == "claude_code_cli"
    assert tg.execution_path_for_tier(3) == "claude_code_cli_headless"
    assert tg.execution_path_for_tier(4) == "claude_code_cli_headless"


def test_dispatch_script_headless_branch_has_a_real_timeout_wrapper():
    src = open(DISPATCH_SH).read()
    assert 'timeout "$CLI_TIMEOUT_SECONDS" claude -p' in src


def test_dispatch_script_headless_branch_registers_cleanup_trap():
    src = open(DISPATCH_SH).read()
    assert "trap _cli_headless_cleanup EXIT" in src
    assert "git -C \"$REPO_PATH\" worktree remove --force \"$CLI_WORKSPACE\"" in src
    assert 'rm -rf "$CLI_TASK_DIR"' in src


def test_dispatch_script_never_invokes_aider_or_openrouter_as_a_real_command():
    src = open(DISPATCH_SH).read()
    executable_lines = [
        line for line in src.splitlines()
        if not line.strip().startswith("#") and re.search(r"\baider\b", line)
    ]
    assert executable_lines == [], (
        "dispatch-owner-task.sh must never invoke aider as a real command "
        f"(only reference it in comments); found: {executable_lines}"
    )
    assert "OPENROUTER_API_KEY=" not in src
    assert "ANTHROPIC_BASE_URL" not in src
