#!/usr/bin/env python3
"""
credit-accountant.py -- Owner directive 2026-07-20: hard $1-increment
approval gate on metered AI credit spend (OpenRouter/GLM-5.2 -- the real,
literally-metered pool that caused the 47-failed-unit RCA earlier this
session, NOT the flat-rate Claude Code CLI subscription used to run this
very script). "Claude Code CLI subscription becomes the accountant,
without Claude Code CLI subscription permission, the mother router cannot
spend any credit."

WHY "Claude Code CLI subscription" is the right accountant, verified not
assumed: this server's claude CLI authenticates via CLAUDE_CODE_OAUTH_TOKEN
(a flat-rate subscription), and ANTHROPIC_API_KEY is explicitly disabled
(ANTHROPIC_API_KEY_DISABLED_PER_OWNER_2026-07-18 in shared/.env) -- there
is no ANTHROPIC_BASE_URL override routing claude -p through OpenRouter
either. So invoking `claude -p` here for judgment calls draws on Rajat's
already-paid-for subscription, not the metered OpenRouter pool this
script exists to protect -- exactly the "use minimal credits, only when
needed" principle applied correctly: gate the metered resource with the
unmetered one, not with more metered spend.

Two gates, matching the Owner's exact described flow:
  1. propose  -- BEFORE any metered spend: mother router (or any
     dispatch path) submits a plan for the next $1 increment. Rejected
     outright, no AI call at all, if a deterministic check already
     answers it (existing capability found via system_index, balance
     insufficient, prior increment not yet approved). Only escalates to
     a real (subscription, not metered) claude -p judgment call when the
     deterministic checks can't decide alone.
  2. report   -- AFTER the $1 is actually spent: the real outcome is
     reported back. Approves or rejects the OUTCOME, which gates whether
     the NEXT increment's propose call is even allowed to run.

FAILS CLOSED, unlike this codebase's other guardrails (which mostly fail
open on their OWN infrastructure failure to avoid blocking legitimate
work): an approval gate whose job is preventing waste must default to
NO SPEND when it cannot render a verdict, not the reverse. A broken
accountant halting all metered spend server-wide is the intended,
safe failure mode -- surfaced as a HIGH-priority health-check-15min.py
anomaly (see check_credit_accountant_health()) so it gets fixed fast
rather than silently blocking work forever, but never bypassed.

Usage:
  credit-accountant.py propose --task-id ID --plan "text" [--repo NAME]
  credit-accountant.py report --task-id ID --increment N --actual-spend-usd X --outcome "text"
  credit-accountant.py status --task-id ID
Exit 0 = approved. Exit 1 = rejected / halted. Exit 2 = usage error.
"""
import argparse
import datetime
import json
import os
import sqlite3
import subprocess
import sys
import urllib.request

import dispatch_core  # 2026-07-29 cron-consolidation-phase6: shared concurrency gate (cmd_prune only)

LEDGER_PATH = "/opt/veridian/ai-os/memory/credit-ledger.sqlite"
MIN_REMAINING_USD = 0.10
CLAUDE_JUDGMENT_TIMEOUT_S = 60
CLAUDE_JUDGMENT_MAX_TOKENS = 300


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def get_db():
    os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
    conn = sqlite3.connect(LEDGER_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS credit_increments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            increment_number INTEGER NOT NULL,
            plan_text TEXT NOT NULL,
            search_terms TEXT,
            plan_verdict TEXT,
            plan_reasoning TEXT,
            plan_reviewer TEXT,
            plan_proposed_at TEXT NOT NULL,
            plan_reviewed_at TEXT,
            actual_spend_usd REAL,
            outcome_summary TEXT,
            outcome_verdict TEXT,
            outcome_reasoning TEXT,
            outcome_reviewer TEXT,
            outcome_reported_at TEXT,
            outcome_reviewed_at TEXT,
            UNIQUE(task_id, increment_number)
        )
    """)
    conn.commit()
    return conn


def get_openrouter_remaining():
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        env_path = "/opt/veridian/shared/.env"
        try:
            with open(env_path) as f:
                for line in f:
                    if line.startswith("OPENROUTER_API_KEY="):
                        key = line.strip().split("=", 1)[1]
                        break
        except FileNotFoundError:
            pass
    if not key:
        return None
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/credits",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
    except Exception:
        return None
    d = data.get("data", {})
    total_credits = d.get("total_credits")
    total_usage = d.get("total_usage")
    if total_credits is None or total_usage is None:
        return None
    return total_credits - total_usage


def check_existing_capability(search_terms):
    """Deterministic, zero-AI-cost check: does system_index already show
    a real mechanism covering this plan? Reuses the existing
    check-duplicate CLI (superboss-register.py) rather than a second,
    parallel search implementation.

    Takes CALLER-SUPPLIED search_terms, not auto-extracted from free-text
    plan prose -- caught via real testing before trusting this script:
    naive "first N words over 4 chars" extraction from a plan like "write
    a haiku about database indexes" pulled in 5 unrelated DATABASE_CATALOG
    entries purely because the common word "database" appears in dozens
    of legitimate registry entries. A curated, caller-supplied search term
    is a real relevance signal; auto-extracted prose nouns are not. This
    also forces the caller (mother router or whatever proposes the plan)
    to actually think about what a search would look for, rather than the
    accountant silently guessing on their behalf.
    """
    if not search_terms:
        return False, None  # caller supplied no terms -- cannot check, do not block on an absent check
    try:
        out = subprocess.run(
            ["python3", "/opt/veridian/scripts/superboss-register.py", "check-duplicate", search_terms],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(out.stdout)
        return data.get("found", 0) > 0, data
    except Exception:
        return False, None  # can't check -- do not block on this specific sub-check's own failure


def get_claude_oauth_env():
    """The worker fleet's systemd services get CLAUDE_CODE_OAUTH_TOKEN via
    EnvironmentFile=/opt/veridian/shared/.env automatically -- but a
    script invoked outside that systemd context (a raw SSH shell, cron
    without the same wrapper) does NOT have it in its ambient environment.
    Confirmed via real testing before trusting this: an interactive SSH
    shell on this exact server has it unset, and the first live test of
    this function failed auth with 'Not logged in' as a direct result.
    Explicitly load it and merge into a subprocess env dict, same
    .env-fallback pattern already used for OPENROUTER_API_KEY in this
    file and in preflight-guard.py -- makes this script correct
    regardless of how it's invoked, not just when systemd happens to be
    the caller."""
    env = os.environ.copy()
    if env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return env
    env_path = "/opt/veridian/shared/.env"
    try:
        with open(env_path) as f:
            for line in f:
                if line.startswith("CLAUDE_CODE_OAUTH_TOKEN="):
                    env["CLAUDE_CODE_OAUTH_TOKEN"] = line.strip().split("=", 1)[1]
                    break
    except FileNotFoundError:
        pass
    return env


def claude_judgment_call(prompt_body):
    """The actual 'Claude Code CLI subscription' accountant call --
    subscription-authenticated (CLAUDE_CODE_OAUTH_TOKEN), NOT the metered
    OpenRouter pool this whole mechanism exists to gate. Bounded prompt,
    bounded tokens, strict expected output shape (PASS/FAIL/REDIRECT +
    one-line reason) so a real subprocess call can be parsed reliably.
    """
    system = (
        "You are the credit accountant for an autonomous AI dev-ops system. "
        "You approve or reject work increments costed against the Claude Max subscription (a shared, rate-limited quota -- "
        "NOT metered OpenRouter/GLM-5.2 credits; that proxy is no longer used for this work as of 2026-07-23). "
        "The concern is wasting AI reasoning effort and subscription quota on trivial or duplicate work, not dollar cost. "
        "Zero tolerance for waste. If existing software/scripts could do this instead of an AI call, say REDIRECT. "
        "Respond with EXACTLY one of: PASS, FAIL, or REDIRECT, followed by a colon and one short sentence of reasoning. "
        "No other text."
    )
    full_prompt = f"{system}\n\n{prompt_body}"
    try:
        result = subprocess.run(
            ["claude", "-p", full_prompt, "--model", "sonnet", "--effort", "high", "--output-format", "json",
             "--max-budget-usd", "0.30"],  # 2026-07-23: raised from 0.05 -- Sonnet 5 High real cache-creation cost intermittently exceeded 0.05 on this same trivial judgment prompt after the GLM-proxy-to-real-subscription auth switch
            capture_output=True, text=True, timeout=CLAUDE_JUDGMENT_TIMEOUT_S,
            env=get_claude_oauth_env(),
        )
        data = json.loads(result.stdout)
        if data.get("is_error"):
            return None, f"claude -p reported is_error: {str(data.get('result',''))[:200]}"
        text = (data.get("result") or "").strip()
        verdict = None
        for candidate in ("PASS", "FAIL", "REDIRECT"):
            if text.upper().startswith(candidate):
                verdict = candidate
                break
        if verdict is None:
            return None, f"unparseable accountant response: {text[:200]}"
        reason = text.split(":", 1)[1].strip() if ":" in text else ""
        return verdict, reason
    except subprocess.TimeoutExpired:
        return None, "accountant claude -p call timed out"
    except Exception as e:
        return None, f"accountant claude -p call failed: {e}"


def cmd_propose(args):
    conn = get_db()
    cur = conn.cursor()

    # Determine increment number: 1 + count of prior increments for this task.
    cur.execute("SELECT COUNT(*) FROM credit_increments WHERE task_id = ?", (args.task_id,))
    increment_number = cur.fetchone()[0] + 1

    if increment_number > 1:
        cur.execute(
            "SELECT outcome_verdict FROM credit_increments WHERE task_id = ? AND increment_number = ?",
            (args.task_id, increment_number - 1),
        )
        row = cur.fetchone()
        prior_outcome = row[0] if row else None
        # HARD block only on an EXPLICIT prior rejection -- a real, deliberate
        # stop signal, same semantics as the existing circuit breaker /
        # budget-cap hard stops elsewhere in this codebase. A prior increment
        # that is simply PENDING (approved but never reported -- the real,
        # common case of the invocation itself crashing/timing out for a
        # reason unrelated to the plan, before worker-entrypoint.sh could
        # call report) does NOT hard-block. Deliberate trade-off, disclosed
        # not hidden: a permanently-deadlocked task (this increment blocked
        # forever because a crash prevented its own report) is a worse
        # failure mode than an occasional imperfectly-tracked increment --
        # and worker-entrypoint.sh own $10 total-cost cap + circuit breaker
        # remain a real backstop against runaway spend regardless.
        if prior_outcome == "rejected":
            result = {"approved": False, "increment_number": increment_number,
                      "reason": f"prior increment {increment_number - 1} was explicitly rejected -- hard stop, needs human review before any further spend on this task"}
            print(json.dumps(result))
            conn.close()
            sys.exit(1)

    plan_proposed_at = now()
    cur.execute(
        "INSERT INTO credit_increments (task_id, increment_number, plan_text, search_terms, plan_proposed_at) VALUES (?, ?, ?, ?, ?)",
        (args.task_id, increment_number, args.plan, args.search_terms, plan_proposed_at),
    )
    conn.commit()

    # Deterministic checks first -- zero AI cost.
    remaining = get_openrouter_remaining()
    if remaining is not None and remaining < MIN_REMAINING_USD:
        verdict, reasoning, reviewer = "rejected", f"OpenRouter balance ${remaining:.4f} below ${MIN_REMAINING_USD} floor -- real money problem, not a code gap", "deterministic"
    else:
        exists, dup_data = check_existing_capability(args.search_terms)
        if exists:
            verdict, reasoning, reviewer = "rejected", "existing software/mechanism already covers this (system_index match) -- use it instead of spending AI credits", "deterministic"
        else:
            claude_verdict, claude_reason = claude_judgment_call(
                f"Task: {args.task_id}\nRepo: {args.repo or 'unspecified'}\n"
                f"Proposed work increment #{increment_number} (costed against Claude Max subscription quota, not dollars):\n{args.plan}\n\n"
                f"Should this increment proceed, given its scope and whether existing capability already covers it?"
            )
            if claude_verdict is None:
                # FAILS CLOSED -- accountant unreachable/unparseable = no approval.
                verdict, reasoning, reviewer = "rejected", f"accountant call failed, failing closed: {claude_reason}", "claude_cli_failed"
            elif claude_verdict == "PASS":
                verdict, reasoning, reviewer = "approved", claude_reason, "claude_cli"
            else:
                verdict, reasoning, reviewer = "rejected", f"{claude_verdict}: {claude_reason}", "claude_cli"

    cur.execute(
        "UPDATE credit_increments SET plan_verdict = ?, plan_reasoning = ?, plan_reviewer = ?, plan_reviewed_at = ? "
        "WHERE task_id = ? AND increment_number = ?",
        (verdict, reasoning, reviewer, now(), args.task_id, increment_number),
    )
    conn.commit()
    conn.close()

    approved = verdict == "approved"
    print(json.dumps({"approved": approved, "increment_number": increment_number, "reason": reasoning, "reviewer": reviewer}))
    sys.exit(0 if approved else 1)


def cmd_report(args):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT plan_verdict FROM credit_increments WHERE task_id = ? AND increment_number = ?",
        (args.task_id, args.increment),
    )
    row = cur.fetchone()
    if row is None or row[0] != "approved":
        print(json.dumps({"approved": False, "reason": "no matching approved plan for this task_id/increment -- report rejected"}))
        conn.close()
        sys.exit(1)

    outcome_reported_at = now()
    cur.execute(
        "UPDATE credit_increments SET actual_spend_usd = ?, outcome_summary = ?, outcome_reported_at = ? "
        "WHERE task_id = ? AND increment_number = ?",
        (args.actual_spend_usd, args.outcome, outcome_reported_at, args.task_id, args.increment),
    )
    conn.commit()

    # Deterministic checks first.
    cur.execute(
        "SELECT outcome_summary FROM credit_increments WHERE task_id = ? AND increment_number < ? ORDER BY increment_number DESC LIMIT 2",
        (args.task_id, args.increment),
    )
    prior_outcomes = [r[0] for r in cur.fetchall() if r[0]]
    repeated_failure = len(prior_outcomes) == 2 and prior_outcomes[0] == prior_outcomes[1] == args.outcome

    if repeated_failure:
        verdict, reasoning, reviewer = "rejected", "identical outcome to the last 2 increments -- circuit-breaker pattern, halting rather than approving further spend", "deterministic"
    elif not args.outcome or len(args.outcome.strip()) < 10:
        verdict, reasoning, reviewer = "rejected", "outcome summary too thin to establish real progress was made", "deterministic"
    else:
        claude_verdict, claude_reason = claude_judgment_call(
            f"Task: {args.task_id}, increment #{args.increment}\n"
            f"Actual spend: ${args.actual_spend_usd}\n"
            f"Reported outcome: {args.outcome}\n\n"
            f"Was this $1 well spent? Approve only if real, proportionate progress was made."
        )
        if claude_verdict is None:
            verdict, reasoning, reviewer = "rejected", f"accountant call failed, failing closed: {claude_reason}", "claude_cli_failed"
        elif claude_verdict == "PASS":
            verdict, reasoning, reviewer = "approved", claude_reason, "claude_cli"
        else:
            verdict, reasoning, reviewer = "rejected", f"{claude_verdict}: {claude_reason}", "claude_cli"

    cur.execute(
        "UPDATE credit_increments SET outcome_verdict = ?, outcome_reasoning = ?, outcome_reviewer = ?, outcome_reviewed_at = ? "
        "WHERE task_id = ? AND increment_number = ?",
        (verdict, reasoning, reviewer, now(), args.task_id, args.increment),
    )
    conn.commit()
    conn.close()

    approved = verdict == "approved"
    print(json.dumps({"approved": approved, "reason": reasoning, "reviewer": reviewer}))
    sys.exit(0 if approved else 1)


def cmd_prune(args):
    """Bounds credit-ledger.sqlite's long-term growth. Safe to run
    frequently (cheap no-op when nothing is old enough to delete) --
    wired into a daily cron via run-logged.sh, same pattern as this
    server's other scheduled maintenance."""
    # 2026-07-29 cron-consolidation-phase6: shared concurrency gate, mirroring
    # dispatch_core.py's acquire_dispatch_lock()/has_free_slot() pattern already
    # used by dispatch-tick.py/phase-continuation-tick.py/status-remediation-tick.py.
    # Scoped to the cron-invoked `prune` subcommand only -- propose/report/status
    # are interactive/automation call sites, not cron jobs, and must not be
    # gated by worker-spawn concurrency. VACUUM below is real, non-trivial I/O.
    with dispatch_core.acquire_dispatch_lock():
        if not dispatch_core.has_free_slot():
            print(json.dumps({"pruned": 0, "skipped": "cap reached, deferring to next scheduled run"}))
            return
    conn = get_db()
    cur = conn.cursor()
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=args.keep_days)).isoformat()
    cur.execute("SELECT COUNT(*) FROM credit_increments WHERE plan_proposed_at < ?", (cutoff,))
    to_delete = cur.fetchone()[0]
    cur.execute("DELETE FROM credit_increments WHERE plan_proposed_at < ?", (cutoff,))
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    print(json.dumps({"pruned": to_delete, "keep_days": args.keep_days, "cutoff": cutoff}))


def cmd_status(args):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT increment_number, plan_verdict, actual_spend_usd, outcome_verdict FROM credit_increments "
        "WHERE task_id = ? ORDER BY increment_number", (args.task_id,),
    )
    rows = cur.fetchall()
    conn.close()
    print(json.dumps([
        {"increment": r[0], "plan_verdict": r[1], "actual_spend_usd": r[2], "outcome_verdict": r[3]}
        for r in rows
    ], indent=2))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_propose = sub.add_parser("propose")
    p_propose.add_argument("--task-id", required=True)
    p_propose.add_argument("--plan", required=True)
    p_propose.add_argument("--search-terms", required=True,
                            help="Curated, specific keywords for the existing-capability check -- "
                                 "NOT auto-extracted from --plan (tested and found too imprecise: "
                                 "common words like 'database' false-matched unrelated entries). "
                                 "The caller must think about what they'd search for.")
    p_propose.add_argument("--repo", default=None)
    p_propose.set_defaults(func=cmd_propose)

    p_report = sub.add_parser("report")
    p_report.add_argument("--task-id", required=True)
    p_report.add_argument("--increment", type=int, required=True)
    p_report.add_argument("--actual-spend-usd", type=float, required=True)
    p_report.add_argument("--outcome", required=True)
    p_report.set_defaults(func=cmd_report)

    p_status = sub.add_parser("status")
    p_status.add_argument("--task-id", required=True)
    p_status.set_defaults(func=cmd_status)

    p_prune = sub.add_parser("prune")
    p_prune.add_argument("--keep-days", type=int, default=30,
                          help="delete increments older than this many days (default 30 -- generous margin over "
                               "health-check-15min.py's own 15-minute credit_accountant lookback window)")
    p_prune.set_defaults(func=cmd_prune)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
