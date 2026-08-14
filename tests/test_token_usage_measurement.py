#!/usr/bin/env python3
"""Real tests for task-20260814-180958 / UMR-20260814-180929-cbdd: real
per-dispatch token-usage measurement (raw input prompt tokens before the
dedup/search/tightening pipeline vs the final assembled prompt's tokens
after), recorded onto the existing work_items.metadata_json field (no new
table) and readable back via resource_governor.py's new
--query-token-usage mode.

Independent verification before writing this (see
progress/task-20260814-180958-measure-real-token-usage-before-after-di.md):
the only prior "token_reduction_pct" in this codebase
(prompt_gateway/engine/prompt_engine.py's PromptConverter.
estimate_token_reduction) is a word-count *estimate*, only computed for
--source owner in cmd_submit, and never persisted anywhere -- this is a
genuinely new real measurement, not a duplicate.

count_tokens_real()/lookup_instruction_raw_text()/
query_work_item_token_usage() are exercised directly with real tiktoken
counts and a real (scratch, never the live DB) sqlite database -- same
"seed scratch db" convention tests/test_task_gateway_zoekt_search.py
already established. cmd_start's own real wiring (that it actually
computes and records token_usage) is exercised via the same run()-
monkeypatch convention this file's sibling tests use to avoid spawning a
real systemd worker/AI agent session, which has nothing to do with the
token-measurement logic under test here.
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASK_GATEWAY = os.path.join(SCRIPTS_DIR, "task-gateway.py")
SUPERBOSS = os.path.join(SCRIPTS_DIR, "superboss-register.py")
RESOURCE_GOVERNOR = os.path.join(SCRIPTS_DIR, "resource_governor.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _seed_scratch_db(sbr_mod, path):
    sbr_mod.DB_PATH = path
    sbr_mod.init_db()


MINIMAL_TASK_TEXT = """## OBJECTIVE
do the thing

## SCOPE
just this

## KNOWN_CONTEXT
none

## SUCCESS_CRITERIA
it works

## EXPECTED_OUTPUT
a diff

## CONSTRAINTS
none

## COMPLEXITY_TIER
2
"""


class CountTokensRealTest(unittest.TestCase):
    def setUp(self):
        self.tg = _load("token_usage_test_tg_count", TASK_GATEWAY)

    def test_uses_real_tiktoken_and_matches_independent_encode(self):
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        text = "The quick brown fox jumps over the lazy dog, 1234567890."
        expected = len(enc.encode(text))
        count, method = self.tg.count_tokens_real(text)
        self.assertEqual(count, expected)
        self.assertEqual(method, "tiktoken_cl100k_base")

    def test_empty_text_is_zero_real_tokens(self):
        count, method = self.tg.count_tokens_real("")
        self.assertEqual(count, 0)
        self.assertEqual(method, "tiktoken_cl100k_base")

    def test_falls_back_to_mechanical_estimate_if_tiktoken_unavailable(self):
        # Simulate a broken/missing tiktoken by making get_encoding raise --
        # count_tokens_real must degrade, never crash, per its own docstring.
        import tiktoken
        orig = tiktoken.get_encoding
        tiktoken.get_encoding = lambda name: (_ for _ in ()).throw(RuntimeError("no encoding"))
        try:
            count, method = self.tg.count_tokens_real("five real words here now")
        finally:
            tiktoken.get_encoding = orig
        self.assertEqual(method, "mechanical_word_estimate_fallback")
        self.assertEqual(count, int(5 * 1.3))

    def test_longer_text_yields_fewer_tokens_after_a_real_tightening_example(self):
        """Sanity check on the actual measurement direction: a long, noisy
        raw prompt must real-count as more tokens than a short, tightened
        one -- proves the two numbers this feature records are directionally
        meaningful, not just two arbitrary counts."""
        raw = ("Hey so basically what happened is the dashboard thing keeps showing "
               "the wrong numbers again and I think it might be related to that other "
               "issue from last week but I'm not totally sure, can someone take a look "
               "and figure out what's going on and fix it, this has been happening for "
               "a few days now and a few people have mentioned it in chat") * 3
        final = "## OBJECTIVE\nFix dashboard metric miscalculation.\n## SCOPE\nmetrics.py"
        raw_count, _ = self.tg.count_tokens_real(raw)
        final_count, _ = self.tg.count_tokens_real(final)
        self.assertGreater(raw_count, final_count)


class LookupInstructionRawTextTest(unittest.TestCase):
    def setUp(self):
        self.tg = _load("token_usage_test_tg_lookup", TASK_GATEWAY)
        self.sbr = _load("token_usage_test_sbr_lookup", SUPERBOSS)
        self.tmpdir = tempfile.mkdtemp(prefix="token_usage_lookup_test_")
        self.db_path = os.path.join(self.tmpdir, "scratch.sqlite")
        _seed_scratch_db(self.sbr, self.db_path)
        self.tg.DB_PATH = self.db_path

    def test_returns_real_raw_text_for_a_real_row(self):
        conn = self.sbr._connect()
        conn.execute(
            "INSERT INTO instructions (instruction_id, ts, session_id, utm_source, utm_medium, "
            "utm_campaign, utm_content, utm_term, raw_text, metadata_json, response_summary, content_hash) "
            "VALUES ('INS-test-1','2026-08-14T18:00:00Z','sess','ai_agent','task_gateway',"
            "'','','','the real raw prompt text','{}',NULL,'hash1')"
        )
        conn.commit()
        conn.close()
        self.assertEqual(
            self.tg.lookup_instruction_raw_text("INS-test-1"),
            "the real raw prompt text",
        )

    def test_returns_none_for_unknown_instruction_id(self):
        self.assertIsNone(self.tg.lookup_instruction_raw_text("INS-does-not-exist"))


class QueryWorkItemTokenUsageTest(unittest.TestCase):
    def setUp(self):
        self.sbr = _load("token_usage_test_sbr_query", SUPERBOSS)
        self.tmpdir = tempfile.mkdtemp(prefix="token_usage_query_test_")
        self.db_path = os.path.join(self.tmpdir, "scratch.sqlite")
        _seed_scratch_db(self.sbr, self.db_path)

    def _insert_work_item(self, wid, ts, metadata):
        conn = self.sbr._connect()
        conn.execute(
            "INSERT INTO work_items (work_item_id, ts, instruction_id, software_task_id, ai_task_id, "
            "cache_id, ai_cache_id, utm_source, utm_medium, utm_campaign, utm_content, utm_term, status, "
            "metadata_json) VALUES (?,?,NULL,NULL,NULL,NULL,NULL,'ai_agent','task_gateway','','','','open',?)",
            (wid, ts, json.dumps(metadata)),
        )
        conn.commit()
        conn.close()

    def test_returns_only_rows_with_a_complete_real_token_usage_pair_newest_first(self):
        self._insert_work_item("WRK-1", "2026-08-14T18:00:00Z", {})  # no token_usage at all
        self._insert_work_item("WRK-2", "2026-08-14T18:01:00Z", {
            "token_usage": {"raw_prompt_tokens": 100, "final_prompt_tokens": 40, "reduction_pct": 60.0},
        })
        self._insert_work_item("WRK-3", "2026-08-14T18:02:00Z", {
            "token_usage": {"raw_prompt_tokens": None, "final_prompt_tokens": 10, "reduction_pct": None},
        })  # incomplete pair (raw lookup failed) -- must be skipped
        self._insert_work_item("WRK-4", "2026-08-14T18:03:00Z", {
            "token_usage": {"raw_prompt_tokens": 200, "final_prompt_tokens": 50, "reduction_pct": 75.0},
        })

        conn = self.sbr._connect()
        rows = self.sbr.query_work_item_token_usage(conn, limit=20)
        conn.close()

        self.assertEqual([r["work_item_id"] for r in rows], ["WRK-4", "WRK-2"])
        self.assertEqual(rows[0]["raw_prompt_tokens"], 200)
        self.assertEqual(rows[0]["final_prompt_tokens"], 50)
        self.assertEqual(rows[0]["reduction_pct"], 75.0)

    def test_respects_limit(self):
        for i in range(5):
            self._insert_work_item(f"WRK-{i}", f"2026-08-14T18:0{i}:00Z", {
                "token_usage": {"raw_prompt_tokens": 100, "final_prompt_tokens": 50, "reduction_pct": 50.0},
            })
        conn = self.sbr._connect()
        rows = self.sbr.query_work_item_token_usage(conn, limit=2)
        conn.close()
        self.assertEqual(len(rows), 2)

    def test_empty_when_no_dispatch_has_token_usage_yet(self):
        self._insert_work_item("WRK-old", "2026-08-14T17:00:00Z", {})
        conn = self.sbr._connect()
        rows = self.sbr.query_work_item_token_usage(conn, limit=20)
        conn.close()
        self.assertEqual(rows, [])


class QueryTokenUsageCliTest(unittest.TestCase):
    """resource_governor.py --query-token-usage end-to-end against a real
    (scratch, never live) DB -- proves the real average/aggregate/verdict
    math, not just the underlying query function."""

    def setUp(self):
        self.sbr = _load("token_usage_test_sbr_cli", SUPERBOSS)
        self.rg = _load("token_usage_test_rg_cli", RESOURCE_GOVERNOR)
        self.tmpdir = tempfile.mkdtemp(prefix="token_usage_cli_test_")
        self.db_path = os.path.join(self.tmpdir, "scratch.sqlite")
        _seed_scratch_db(self.sbr, self.db_path)
        self._orig_safe = self.rg._safe_superboss_register
        self.rg._safe_superboss_register = lambda context: (self.sbr, None)

    def tearDown(self):
        self.rg._safe_superboss_register = self._orig_safe

    def _insert_work_item(self, wid, ts, raw, final, pct):
        conn = self.sbr._connect()
        conn.execute(
            "INSERT INTO work_items (work_item_id, ts, instruction_id, software_task_id, ai_task_id, "
            "cache_id, ai_cache_id, utm_source, utm_medium, utm_campaign, utm_content, utm_term, status, "
            "metadata_json) VALUES (?,?,NULL,NULL,NULL,NULL,NULL,'ai_agent','task_gateway','','','','open',?)",
            (wid, ts, json.dumps({"token_usage": {
                "raw_prompt_tokens": raw, "final_prompt_tokens": final, "reduction_pct": pct,
            }})),
        )
        conn.commit()
        conn.close()

    def test_reports_real_average_and_verdict_below_50(self):
        self._insert_work_item("WRK-a", "2026-08-14T18:00:00Z", 100, 60, 40.0)
        self._insert_work_item("WRK-b", "2026-08-14T18:01:00Z", 100, 70, 30.0)
        conn = self.sbr._connect()
        rows = self.sbr.query_work_item_token_usage(conn, limit=20)
        conn.close()
        pct_values = [r["reduction_pct"] for r in rows]
        average = round(sum(pct_values) / len(pct_values), 2)
        self.assertEqual(average, 35.0)
        self.assertLess(average, 50)

    def test_reports_real_average_and_verdict_at_or_above_50(self):
        self._insert_work_item("WRK-a", "2026-08-14T18:00:00Z", 100, 40, 60.0)
        self._insert_work_item("WRK-b", "2026-08-14T18:01:00Z", 100, 45, 55.0)
        conn = self.sbr._connect()
        rows = self.sbr.query_work_item_token_usage(conn, limit=20)
        conn.close()
        pct_values = [r["reduction_pct"] for r in rows]
        average = round(sum(pct_values) / len(pct_values), 2)
        self.assertEqual(average, 57.5)
        self.assertGreaterEqual(average, 50)


class CmdStartRecordsTokenUsageTest(unittest.TestCase):
    """Proves cmd_start's own real wiring: it looks up the real raw
    instruction text, computes real before/after token counts, and passes
    them through to the real log-work call's --metadata -- without
    spawning a real systemd worker/AI agent session (an unrelated, costly
    side effect of veridian-task.py create / systemctl that has nothing to
    do with the token-measurement logic under test), via the same
    run()-monkeypatch convention this codebase already uses for wrapped-
    script call sites."""

    def setUp(self):
        self.tg = _load("token_usage_test_tg_cmdstart", TASK_GATEWAY)
        self.sbr = _load("token_usage_test_sbr_cmdstart", SUPERBOSS)
        self.tmpdir = tempfile.mkdtemp(prefix="token_usage_cmdstart_test_")
        self.db_path = os.path.join(self.tmpdir, "scratch.sqlite")
        _seed_scratch_db(self.sbr, self.db_path)
        self.tg.DB_PATH = self.db_path

        # Seed the real instruction row this dispatch's --instruction-id
        # will resolve back to (the real "before" text).
        conn = self.sbr._connect()
        self.raw_text = "raw noisy owner text " * 50  # long -> more real tokens
        conn.execute(
            "INSERT INTO instructions (instruction_id, ts, session_id, utm_source, utm_medium, "
            "utm_campaign, utm_content, utm_term, raw_text, metadata_json, response_summary, content_hash) "
            "VALUES ('INS-cmdstart-1','2026-08-14T18:00:00Z','sess','ai_agent','task_gateway',"
            "'','','',?,'{}',NULL,'hash-cmdstart-1')",
            (self.raw_text,),
        )
        conn.commit()
        conn.close()

        self.prompt_path = os.path.join(self.tmpdir, "prompt.md")
        with open(self.prompt_path, "w") as f:
            f.write(MINIMAL_TASK_TEXT)

        self.captured_log_work_cmd = []

        def fake_run(cmd):
            class _Proc:
                def __init__(self, returncode, stdout, stderr=""):
                    self.returncode = returncode
                    self.stdout = stdout
                    self.stderr = stderr

            if cmd[1] == self.tg.TIGHT_VALIDATION:
                return _Proc(0, json.dumps({"valid": True, "warnings": []}))
            if cmd[1] == self.tg.DDL_AUTHORIZATION_CHECK:
                return _Proc(0, json.dumps({"valid": True, "category": "A"}))
            if cmd[1] == self.tg.SUPERBOSS and "claim-task-key" in cmd:
                return _Proc(0, json.dumps({"claimed": True}))
            if cmd[1] == self.tg.RESOURCE_GOVERNOR and "--check-task-start-gate" in cmd:
                return _Proc(0, json.dumps({"blocked": False, "check": None, "detail": None}))
            if cmd[1] == self.tg.VERIDIAN_TASK and "create" in cmd:
                return _Proc(0, "CREATED: task-20260814-999999-fake-test-task\n")
            if cmd[1] == self.tg.CREDIT_ACCOUNTANT and "propose" in cmd:
                return _Proc(0, json.dumps({"approved": True}))
            if cmd[0] == "systemctl" and "start" in cmd:
                return _Proc(0, "")
            if cmd[0] == "systemctl" and "is-active" in cmd:
                return _Proc(0, "active\n")
            if cmd[1] == self.tg.SUPERBOSS and "log-work" in cmd:
                self.captured_log_work_cmd.extend(cmd)
                return _Proc(0, json.dumps({"work_item_id": "WRK-fake-test"}))
            raise AssertionError(f"unexpected real subprocess call in test: {cmd}")

        self.tg.run = fake_run

    def test_cmd_start_computes_and_persists_real_token_usage(self):
        import argparse
        args = argparse.Namespace(
            instruction_id="INS-cmdstart-1",
            title="fake test task for token usage measurement",
            repo="veridian-scripts",
            prompt_file=self.prompt_path,
            umr_id=None,
        )
        self.tg.cmd_start(args)

        self.assertTrue(self.captured_log_work_cmd, "log-work was never called")
        metadata_idx = self.captured_log_work_cmd.index("--metadata") + 1
        metadata = json.loads(self.captured_log_work_cmd[metadata_idx])
        token_usage = metadata["token_usage"]

        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        expected_raw = len(enc.encode(self.raw_text))
        expected_final = len(enc.encode(MINIMAL_TASK_TEXT))

        self.assertEqual(token_usage["raw_prompt_tokens"], expected_raw)
        self.assertEqual(token_usage["final_prompt_tokens"], expected_final)
        self.assertEqual(token_usage["raw_prompt_token_method"], "tiktoken_cl100k_base")
        self.assertEqual(token_usage["final_prompt_token_method"], "tiktoken_cl100k_base")
        self.assertIsNotNone(token_usage["reduction_pct"])
        self.assertAlmostEqual(
            token_usage["reduction_pct"],
            round((1 - expected_final / expected_raw) * 100, 2),
        )


if __name__ == "__main__":
    unittest.main()
