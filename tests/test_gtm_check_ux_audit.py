"""Unit tests for gtm_check_ux_audit.py's pure logic -- evidence curation,
prompt/response parsing, and the pass/fail/blocked aggregation rule.

These tests never touch the live site or invoke a real `claude -p` call --
they exercise the deterministic, mockable seams only, same spirit as
test_generate_pm_report_v3.py's is_claude_dash_p_argv()-style unit tests.
"""
import importlib.util
import json
import os
import sys

import pytest

MODULE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gtm_check_ux_audit.py")
spec = importlib.util.spec_from_file_location("gtm_check_ux_audit", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules["gtm_check_ux_audit"] = mod
spec.loader.exec_module(mod)


def make_probe_result():
    return {
        "generatedAt": "2026-08-06T00:00:00Z",
        "pages": [
            {
                "path": "/login", "httpStatus": 200, "loadOk": True, "finalUrl": "https://projexa-ai.com/login",
                "redirected": False, "loadTimeMs": 400, "consoleErrors": [], "pageErrors": [],
                "title": "Log in", "metaDescription": None, "h1Count": 1,
                "headings": [{"tag": "h1", "text": "Log in"}], "navLinks": [], "footerLinks": [],
                "helpLinks": [], "forms": [{"action": None, "hasNovalidate": False, "fields": [
                    {"type": "email", "name": "email", "required": True, "pattern": None, "autocomplete": "username", "placeholder": "Email", "hasLabel": True},
                    {"type": "password", "name": "password", "required": True, "pattern": None, "autocomplete": "current-password", "placeholder": "Password", "hasLabel": True},
                ]}], "buttons": [{"text": "Log in", "disabled": False}],
                "liveRegionCount": 0, "loadingIndicatorCount": 0, "hasSkipLink": False,
                "imageCount": 0, "imagesMissingAlt": 0, "positiveTabindexCount": 0,
                "domNodeCount": 120, "visibleTextLength": 300, "mentionsKeyboardShortcuts": False,
            },
            {
                "path": "/help", "httpStatus": 200, "loadOk": True, "finalUrl": "https://projexa-ai.com/login?redirectTo=%2Fhelp",
                "redirected": True, "loadTimeMs": 350, "consoleErrors": [], "pageErrors": [],
                "title": "Log in", "metaDescription": None, "h1Count": 1,
                "headings": [{"tag": "h1", "text": "Log in"}], "navLinks": [], "footerLinks": [],
                "helpLinks": [], "forms": [], "buttons": [],
                "liveRegionCount": 0, "loadingIndicatorCount": 0, "hasSkipLink": False,
                "imageCount": 0, "imagesMissingAlt": 0, "positiveTabindexCount": 0,
                "domNodeCount": 100, "visibleTextLength": 200, "mentionsKeyboardShortcuts": False,
            },
        ],
        "errorPageProbe": {
            "path": "/__gtm_ux_audit_nonexistent_page_probe__", "httpStatus": 404, "loadOk": False,
            "finalUrl": "https://projexa-ai.com/__gtm_ux_audit_nonexistent_page_probe__", "redirected": False,
            "consoleErrors": [], "pageErrors": [],
        },
        "error": None,
    }


class TestBuildHeuristicEvidence:
    def test_heuristic_1_uses_visibility_keys(self):
        h = next(x for x in mod.HEURISTICS if x["id"] == 1)
        ev = mod.build_heuristic_evidence(h, make_probe_result())
        assert "pages" in ev
        assert ev["pages"][0]["liveRegionCount"] == 0
        assert ev["pages"][0]["loadingIndicatorCount"] == 0
        assert "forms" not in ev["pages"][0]

    def test_heuristic_9_uses_error_page_probe(self):
        h = next(x for x in mod.HEURISTICS if x["id"] == 9)
        ev = mod.build_heuristic_evidence(h, make_probe_result())
        assert ev["error_page_probe"]["httpStatus"] == 404
        assert len(ev["pages_live_region_counts"]) == 2

    def test_heuristic_10_shows_help_redirect(self):
        h = next(x for x in mod.HEURISTICS if x["id"] == 10)
        ev = mod.build_heuristic_evidence(h, make_probe_result())
        help_page = next(p for p in ev["pages"] if p["path"] == "/help")
        assert help_page["redirected"] is True
        assert "login" in help_page["finalUrl"]

    def test_all_ten_heuristics_produce_evidence_without_error(self):
        pr = make_probe_result()
        for h in mod.HEURISTICS:
            ev = mod.build_heuristic_evidence(h, pr)
            assert isinstance(ev, dict)


class TestBuildPrompt:
    def test_prompt_includes_fixed_rubric_and_evidence(self):
        h = next(x for x in mod.HEURISTICS if x["id"] == 1)
        ev = mod.build_heuristic_evidence(h, make_probe_result())
        prompt = mod.build_prompt(h, ev)
        assert "Visibility of system status" in prompt
        assert "Maze" in prompt
        assert "4 = " in prompt
        assert '"severity"' in prompt
        assert json.dumps(ev, indent=2)[:200] in prompt

    def test_prompt_is_identical_for_identical_evidence(self):
        h = next(x for x in mod.HEURISTICS if x["id"] == 4)
        ev = mod.build_heuristic_evidence(h, make_probe_result())
        p1 = mod.build_prompt(h, ev)
        p2 = mod.build_prompt(h, ev)
        assert p1 == p2


class TestParseClaudeJsonResponse:
    def test_parses_plain_json(self):
        raw = '{"severity": 2, "findings": [{"description": "x", "severity": 2, "page": "/login"}], "rationale": "because"}'
        obj = mod.parse_claude_json_response(raw)
        assert obj["severity"] == 2
        assert len(obj["findings"]) == 1

    def test_strips_markdown_fences(self):
        raw = '```json\n{"severity": 0, "findings": []}\n```'
        obj = mod.parse_claude_json_response(raw)
        assert obj["severity"] == 0
        assert obj["findings"] == []
        assert obj["rationale"] == ""

    def test_rejects_missing_severity(self):
        with pytest.raises(ValueError):
            mod.parse_claude_json_response('{"findings": []}')

    def test_rejects_out_of_range_severity(self):
        with pytest.raises(ValueError):
            mod.parse_claude_json_response('{"severity": 7, "findings": []}')

    def test_rejects_non_json(self):
        with pytest.raises(json.JSONDecodeError):
            mod.parse_claude_json_response("not json at all")

    def test_tolerates_trailing_text_after_well_formed_object(self):
        # Real observed failure mode (heuristic 8, live run 2026-08-06): the
        # model emitted a complete, valid JSON object immediately followed by
        # extra trailing prose despite being told to return ONLY the object.
        # A well-formed leading object must still parse -- trailing junk is
        # not a reason to blocked-out an otherwise real, scored heuristic.
        raw = '{"severity": 1, "findings": [{"description": "x", "severity": 1, "page": "all"}], "rationale": "y"} some trailing remark the model appended anyway'
        obj = mod.parse_claude_json_response(raw)
        assert obj["severity"] == 1
        assert len(obj["findings"]) == 1

    def test_does_not_tolerate_leading_junk(self):
        with pytest.raises(json.JSONDecodeError):
            mod.parse_claude_json_response('Sure, here is the JSON: {"severity": 1, "findings": []}')


class TestAggregateHeuristicOutputs:
    def _ok(self, sev, findings=None):
        return {"status": "ok", "severity": sev, "findings": findings or [], "error": None}

    def _blocked(self, err="tool absent"):
        return {"status": "blocked", "severity": None, "findings": [], "error": err}

    def test_all_pass_zero_severity(self):
        outputs = {i: self._ok(0) for i in range(1, 11)}
        result, max_sev, fail_ids, blocked_ids = mod.aggregate_heuristic_outputs(outputs)
        assert result == "pass"
        assert max_sev == 0
        assert fail_ids == []
        assert blocked_ids == []

    def test_pass_with_only_cosmetic_and_minor_findings(self):
        outputs = {i: self._ok(0) for i in range(1, 11)}
        outputs[2] = self._ok(1)
        outputs[6] = self._ok(2)
        result, max_sev, fail_ids, blocked_ids = mod.aggregate_heuristic_outputs(outputs)
        assert result == "pass"
        assert max_sev == 2
        assert fail_ids == []

    def test_single_severity_3_finding_fails_whole_run(self):
        outputs = {i: self._ok(0) for i in range(1, 11)}
        outputs[9] = self._ok(3)
        result, max_sev, fail_ids, blocked_ids = mod.aggregate_heuristic_outputs(outputs)
        assert result == "fail"
        assert max_sev == 3
        assert fail_ids == [9]

    def test_severity_4_finding_fails_whole_run(self):
        outputs = {i: self._ok(0) for i in range(1, 11)}
        outputs[1] = self._ok(4)
        result, max_sev, fail_ids, blocked_ids = mod.aggregate_heuristic_outputs(outputs)
        assert result == "fail"
        assert fail_ids == [1]

    def test_multiple_failing_heuristics_all_listed(self):
        outputs = {i: self._ok(0) for i in range(1, 11)}
        outputs[1] = self._ok(3)
        outputs[8] = self._ok(4)
        result, max_sev, fail_ids, blocked_ids = mod.aggregate_heuristic_outputs(outputs)
        assert result == "fail"
        assert max_sev == 4
        assert fail_ids == [1, 8]

    def test_any_blocked_heuristic_blocks_whole_run_even_with_a_fail_present(self):
        outputs = {i: self._ok(0) for i in range(1, 11)}
        outputs[1] = self._ok(4)  # would otherwise be a fail
        outputs[5] = self._blocked("timed out")
        result, max_sev, fail_ids, blocked_ids = mod.aggregate_heuristic_outputs(outputs)
        assert result == "blocked"
        assert max_sev is None
        assert fail_ids == []
        assert blocked_ids == [5]

    def test_blocked_never_silently_reported_as_pass(self):
        outputs = {i: self._blocked() for i in range(1, 11)}
        result, _, _, blocked_ids = mod.aggregate_heuristic_outputs(outputs)
        assert result == "blocked"
        assert len(blocked_ids) == 10


class TestCallClaudeJudgmentParsing:
    """Exercises call_claude_judgment()'s own response handling by mocking
    subprocess.run -- never a real claude -p call."""

    def test_is_error_response_returns_error(self, monkeypatch):
        class FakeCompleted:
            returncode = 0
            stdout = json.dumps({"is_error": True, "result": "boom"})
            stderr = ""

        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: FakeCompleted())
        parsed, err = mod.call_claude_judgment("prompt", {}, 0.3, 10)
        assert parsed is None
        assert "is_error" in err

    def test_well_formed_response_parses(self, monkeypatch):
        class FakeCompleted:
            returncode = 0
            stdout = json.dumps({"is_error": False, "result": '{"severity": 1, "findings": [], "rationale": "fine"}'})
            stderr = ""

        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: FakeCompleted())
        parsed, err = mod.call_claude_judgment("prompt", {}, 0.3, 10)
        assert err is None
        assert parsed["severity"] == 1

    def test_outer_json_decode_failure_returns_error(self, monkeypatch):
        class FakeCompleted:
            returncode = 1
            stdout = "not json"
            stderr = "some crash"

        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: FakeCompleted())
        parsed, err = mod.call_claude_judgment("prompt", {}, 0.3, 10)
        assert parsed is None
        assert err is not None

    def test_timeout_returns_error(self, monkeypatch):
        import subprocess as sp

        def raise_timeout(*a, **k):
            raise sp.TimeoutExpired(cmd="claude", timeout=10)

        monkeypatch.setattr(mod.subprocess, "run", raise_timeout)
        parsed, err = mod.call_claude_judgment("prompt", {}, 0.3, 10)
        assert parsed is None
        assert "timed out" in err
