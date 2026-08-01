#!/usr/bin/env python3
"""
Pre-flight guard, run BEFORE the main claude -p invocation in
worker-entrypoint.sh. Deployed 2026-07-20 per Owner zero-waste directive.

Two failure classes were found in the RCA that this exists to catch before
they cost anything:
  - "instant environment failure" (Group B): 5 tasks, 2-8s each, zero output,
    all failing the same minute under resource contention. Caught here by
    static checks -- $0, no model call.
  - "pathological retry" (Group A): one task retried an unfixed approach 12
    times, burning 2.3 hours. Caught here by the circuit breaker -- refuses
    to start a 3rd attempt at an approach that failed identically twice.

Usage: preflight-guard.py <task_dir> <workspace> [proxy_url]
Exit 0 = proceed (JSON {"proceed": true, ...} on stdout).
Exit 1 = abort (JSON {"proceed": false, "reason": ..., "detail": ...} on
         stdout) -- caller checkpoints as blocked using this and does NOT
         invoke the model.
"""
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
import subprocess
import yaml
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from policy_decision import emit_allow, emit_deny  # noqa: E402


def fail(reason, detail=""):
    # Phase 2 (ai-os/20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml,
    # policy_rule_decision_unification): emits the shared policy_decision_schema
    # envelope ADDITIVELY -- proceed/reason/detail keys are printed exactly as
    # before this refactor (worker-entrypoint.sh and other callers parse those
    # 3 keys with dict.get(), so extra keys are safe, but removing/renaming the
    # originals would not be). schema_version/source_gate/reason_code/decision
    # are new keys a future caller can read without needing to change today.
    decision = emit_deny(source_gate="preflight-guard.py", reason_code=reason, detail=str(detail))
    out = decision.to_dict()
    out.update({"proceed": False, "reason": reason, "detail": str(detail)})
    print(json.dumps(out))
    sys.exit(1)


def ok(detail=""):
    decision = emit_allow(source_gate="preflight-guard.py", detail=detail)
    out = decision.to_dict()
    out.update({"proceed": True, "detail": detail})
    print(json.dumps(out))
    sys.exit(0)


def check_circuit_breaker(task_dir):
    """Refuse to proceed if the last 2 recorded failures have the identical
    signature -- a 3rd identical attempt is a stop signal, not a retry
    signal (see COST-CONTROL.md Q1/Q4)."""
    sig_file = os.path.join(task_dir, ".failure_signatures.json")
    if not os.path.exists(sig_file):
        return
    try:
        with open(sig_file) as f:
            sigs = json.load(f)
    except Exception:
        return
    if len(sigs) >= 2 and sigs[-1] and sigs[-1] == sigs[-2]:
        fail("circuit_breaker_tripped",
             f"last 2 failures had the identical signature -- needs a different approach or human review, "
             f"not a 3rd blind retry. signature={sigs[-1][:100]}")


def check_disk(workspace, min_free_mb=500):
    try:
        usage = shutil.disk_usage(workspace)
    except FileNotFoundError:
        return  # workspace not created yet -- not this guard's concern
    free_mb = usage.free / (1024 * 1024)
    if free_mb < min_free_mb:
        fail("disk_low", f"{free_mb:.0f}MB free at {workspace}, need >={min_free_mb}MB")


def check_mem(min_available_mb=300):
    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                meminfo[k.strip()] = int(v.strip().split()[0])  # kB
        avail_mb = meminfo.get("MemAvailable", 0) / 1024
    except Exception:
        return  # can't read /proc/meminfo -- don't block on a check that can't run
    if avail_mb < min_available_mb:
        fail("memory_low", f"{avail_mb:.0f}MB available, need >={min_available_mb}MB -- "
                            f"likely resource contention from concurrent workers (Group B failure pattern)")


def check_tight_task_schema(task_dir):
    """Faithful Python port of task-tightening.ts's validateTightTask() --
    closes the gap found in the constitution cross-check audit (2026-07-20):
    the shell-layer fleet had zero access to this TS/DB-backed validation.
    Only blocks NEW-format prompts (## OBJECTIVE / ## SCOPE / etc. labeled
    headers) -- a legacy free-text prompt is never retroactively failed."""
    prompt_path = os.path.join(task_dir, "prompt.txt")
    if not os.path.exists(prompt_path):
        return
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import tight_task_validation as ttv
        with open(prompt_path) as f:
            text = f.read()
        fields = ttv.parse_labeled_fields(text)
        if fields is None:
            return  # legacy free-text prompt -- not this validator's concern
        result = ttv.validate_tight_task(fields)
        if not result.get("valid"):
            fail("tight_task_schema_violation", f"{result.get('reason')} {result.get('guidance')}")
    except ImportError:
        return  # validator module unavailable -- fail open, don't block on infra issue


def check_worktree(workspace):
    lock = os.path.join(workspace, ".git", "index.lock")
    if os.path.exists(lock):
        fail("worktree_locked", lock)


def check_single_protocol_file():
    """Owner directive 2026-07-26: exactly ONE Owner<->AI protocol file and exactly
    ONE Owner<->AI memory file under ai-os/OWNER_DIRECTIVES/, enforced as real
    software rather than a one-time manual cleanup (see
    ai-os/OWNER_DIRECTIVES/PROTOCOL_OWNER_AI.yaml source_inventory for the
    consolidation this check protects). Delegates the actual signature/count logic
    to scripts/check_single_protocol_file.py so there is exactly one real
    implementation, callable standalone (SUCCESS_CRITERIA: `python3
    scripts/check_single_protocol_file.py`) or in-process here.
    """
    try:
        from check_single_protocol_file import check as check_protocol_docs
    except ImportError:
        return  # checker module unavailable -- fail open, don't block on infra issue
    ok, result = check_protocol_docs()
    if not ok:
        fail("owner_ai_protocol_file_drift", "; ".join(result["problems"]))


CRONTAB_APPROVED_SNAPSHOT_PATH = "/opt/veridian/ai-os/CRONTAB_APPROVED_SNAPSHOT.txt"
OWNER_DECISIONS_PATH = "/opt/veridian/ai-os/OWNER_DECISIONS_NEEDED_2026-07-23.yaml"
_CRONTAB_CITATION_RE = re.compile(
    r"OWNER_DECISIONS_NEEDED_2026-07-23\.yaml entry id=([A-Za-z0-9_-]+) status=approved"
)


def check_crontab_unauthorized_change(task_dir, snapshot_path=CRONTAB_APPROVED_SNAPSHOT_PATH,
                                       decisions_path=OWNER_DECISIONS_PATH, crontab_cmd=None):
    """Governance item 50: AI_ENGINEERING_POLICY.yaml's automation_scope_boundary.
    standing_exceptions.genuinely_irreversible_actions names crontab modifications
    as a standing Owner-confirmation exception -- "never silently automated
    through, regardless of any other directive's '100% automation' framing."
    Until this check existed, nothing on this host mechanically enforced that;
    it depended entirely on assistant judgment at the moment of action.

    Compares live `crontab -l` output against the last-known-approved snapshot
    at `snapshot_path`. Any difference FAILS CLOSED unless the task's own
    prompt.txt contains the exact citation string:
        OWNER_DECISIONS_NEEDED_2026-07-23.yaml entry id=<id> status=approved
    -- and that citation is independently verified against the REAL, live
    OWNER_DECISIONS_NEEDED_2026-07-23.yaml file (that entry must actually have
    status: approved there). A task's own prompt claiming approval is never
    sufficient by itself -- this check does not trust an unverified claim.

    Fails OPEN only on infrastructure problems that make the check itself
    unable to run (no `crontab` binary, no snapshot baseline seeded yet,
    decisions file unreadable while a valid-looking citation is present) --
    it never fails open on an actual, confirmed live/snapshot mismatch with
    no verified citation, since that is precisely the silent-change scenario
    this policy exists to catch.
    """
    cmd = crontab_cmd or ["crontab", "-l"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return  # no crontab for this user / crontab unreadable -- not this check's concern
        live = result.stdout
    except Exception:
        return  # can't read the live crontab -- fail open, don't block on an infra hiccup

    if not os.path.exists(snapshot_path):
        return  # no approved baseline seeded yet -- nothing to diff against

    try:
        with open(snapshot_path) as f:
            approved = f.read()
    except Exception:
        return

    if live == approved:
        return  # matches the last-known-approved baseline -- no change to gate

    prompt_path = os.path.join(task_dir, "prompt.txt")
    citation_id = None
    if os.path.exists(prompt_path):
        try:
            with open(prompt_path) as f:
                prompt_text = f.read()
            m = _CRONTAB_CITATION_RE.search(prompt_text)
            if m:
                citation_id = m.group(1)
        except Exception:
            citation_id = None

    if citation_id:
        try:
            with open(decisions_path) as f:
                decisions_doc = yaml.safe_load(f) or {}
            for entry in decisions_doc.get("decisions", []):
                if entry.get("id") == citation_id and entry.get("status") == "approved":
                    return  # verified against the real file, not just the prompt's claim
        except Exception:
            pass  # can't verify the citation -- falls through to fail closed below

    fail("crontab_unauthorized_change",
         f"live crontab differs from the last-known-approved snapshot at {snapshot_path}, and "
         f"prompt.txt does not cite a verified-approved {os.path.basename(decisions_path)} entry "
         f"for this exact change. Per AI_ENGINEERING_POLICY.yaml's genuinely_irreversible_actions "
         f"standing exception, crontab modifications require explicit Owner confirmation before "
         f"execution -- this is not a code bug, it is the policy working as designed. If this "
         f"change was Owner-approved, cite it in prompt.txt as: "
         f"'OWNER_DECISIONS_NEEDED_2026-07-23.yaml entry id=<id> status=approved' and ensure that "
         f"entry's status is actually 'approved' in the live decisions file.")


def check_credit_accountant_approval(task_dir):
    """Owner directive 2026-07-20: "without Claude Code CLI subscription
    permission, the mother router cannot spend any credit." This is the
    single, universal enforcement point -- every dispatch path
    (worker-entrypoint.sh AND doc-worker-entrypoint.sh) already calls
    preflight-guard.py, so inserting the gate here means no individual
    dispatcher (queue-dispatcher.py, master-decompose.py, etc.) needs to
    be taught about the accountant separately -- it cannot be forgotten
    or bypassed by a caller that doesn't know it exists. Same
    "middleware/aspect" choke-point pattern already used for
    run-logged.sh and the auto-logging mechanisms this session.

    Auto-derives a plan + search terms from the task's own prompt.txt
    (tight-task-schema OBJECTIVE/SCOPE fields when present, else the
    first 300 chars) -- no caller needs to change to submit a plan
    manually; this check does it on their behalf using data that
    already exists for every task.

    FAILS CLOSED like credit-accountant.py itself: if the accountant
    call errors/times out, this check treats it as a rejection, not a
    pass-through. Zero tolerance means zero silent bypass on
    infrastructure hiccups here specifically -- unlike balance/proxy
    checks elsewhere in this file, which correctly fail open.
    """
    task_id = os.path.basename(os.path.normpath(task_dir))
    prompt_path = os.path.join(task_dir, "prompt.txt")
    plan_text = None
    search_terms = task_id.replace("-", " ").replace("_", " ")
    if os.path.exists(prompt_path):
        try:
            with open(prompt_path) as f:
                text = f.read()
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import tight_task_validation as ttv
            fields = ttv.parse_labeled_fields(text)
            if fields and fields.get("objective"):
                plan_text = fields["objective"][:500]
                if fields.get("scope"):
                    # Fix 2026-07-21: raw prose (fields["scope"][:200]) false-matched
                    # 23 unrelated system_index entries on common words like "add"/
                    # "files"/"schema" -- check-duplicate's own docstring warns this
                    # is imprecise. Extract identifier-like tokens (snake_case,
                    # dotted/slashed paths, backtick-quoted names) instead -- these
                    # are the actual specific, curated keywords the existing-capability
                    # check needs. Falls back to raw prose only if no such tokens
                    # exist, so this never becomes MORE permissive than before.
                    import re as _re
                    tokens = _re.findall(r"[A-Za-z][A-Za-z0-9_./-]{3,}", fields["scope"])
                    # Require an underscore or slash specifically -- a bare dot or a
                    # capitalized-first-letter is too weak a signal (live-tested: a
                    # plain capitalized word like "Files" or "Drizzle" alone matched
                    # 7 and 3 unrelated system_index entries respectively purely on
                    # generic word overlap). snake_case identifiers and file paths
                    # are the real specific signal this check needs.
                    tokens = [t for t in tokens if "_" in t or "/" in t]
                    seen = []
                    for t in tokens:
                        if t not in seen:
                            seen.append(t)
                        if len(seen) >= 10:
                            break
                    search_terms = " ".join(seen) if seen else fields["scope"][:200]
            else:
                plan_text = text[:500]
        except Exception:
            try:
                with open(prompt_path) as f:
                    plan_text = f.read()[:500]
            except Exception:
                plan_text = f"task {task_id}, prompt unreadable"
    else:
        plan_text = f"task {task_id}, no prompt.txt found at preflight time"

    try:
        result = subprocess.run(
            ["python3", "/opt/veridian/scripts/credit-accountant.py", "propose",
             "--task-id", task_id, "--plan", plan_text, "--search-terms", search_terms],
            capture_output=True, text=True, timeout=90,
        )
        if result.returncode != 0:
            try:
                data = json.loads(result.stdout)
                reason = data.get("reason", "no reason given")
            except Exception:
                reason = f"unparseable accountant output: {result.stdout[:200]} {result.stderr[:200]}"
            fail("credit_accountant_rejected", reason)
    except subprocess.TimeoutExpired:
        fail("credit_accountant_unreachable", "credit-accountant.py propose call timed out -- failing closed, no spend without approval")
    except Exception as e:
        fail("credit_accountant_unreachable", f"credit-accountant.py propose call failed: {e} -- failing closed, no spend without approval")



def log_subscription_usage(task_dir):
    """2026-07-23 Owner directive: subscription-billed work is never gated by
    the credit-accountant $1-increment check -- that gate exists only for
    metered API spend (OpenRouter/GLM/GroqCloud/Cerebras/Z.ai/OpenAI/
    Anthropic-Console-API/any pay-per-token API). Log-only."""
    task_id = os.path.basename(os.path.normpath(task_dir))
    log_path = "/opt/veridian/ai-os/logs/subscription-usage.jsonl"
    try:
        with open(log_path, "a") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "task_id": task_id,
                "billing": "claude_max_subscription",
                "accountant_gate": "skipped_per_owner_directive_2026-07-23",
            }) + "\n")
    except Exception:
        pass


def check_proxy_health(proxy_url):
    try:
        with urllib.request.urlopen(f"{proxy_url}/healthz", timeout=5) as r:
            data = json.loads(r.read())
    except Exception as e:
        fail("proxy_unreachable", f"{proxy_url}/healthz: {e}")
    if data.get("budget_allowed") is False:
        fail("budget_exhausted",
             f"spent ${data.get('budget_spent_usd')} >= cap ${data.get('budget_cap_usd')} -- "
             f"this is the hard ceiling working as designed, not a bug")


def canary_call(proxy_url):
    """One minimal real call through the actual call path -- confirms model
    reachability, auth, and tool-schema handling before the full task prompt
    (which can be tens of thousands of tokens) goes out. Costs a fraction of
    a cent; a cache hit on a repeat canary costs nothing at all."""
    payload = {
        "model": "claude-opus-4-8",
        "max_tokens": 5,
        "messages": [{"role": "user", "content": "canary: reply OK"}],
    }
    req = urllib.request.Request(
        f"{proxy_url}/v1/messages",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode(errors="replace")
        fail("canary_call_http_error", f"{e.code}: {err[:200]}")
        return
    except Exception as e:
        fail("canary_call_failed", str(e))
        return
    if resp.get("type") == "error":
        fail("canary_call_error", resp.get("error", {}).get("message", ""))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        fail("bad_invocation", "usage: preflight-guard.py <task_dir> <workspace> [proxy_url|--no-proxy]")
    task_dir_arg = sys.argv[1]
    workspace_arg = sys.argv[2]
    proxy_arg = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:8787"

    check_circuit_breaker(task_dir_arg)
    check_tight_task_schema(task_dir_arg)
    check_disk(workspace_arg)
    check_mem()
    check_worktree(workspace_arg)
    check_crontab_unauthorized_change(task_dir_arg)
    check_single_protocol_file()

    if proxy_arg == "--no-proxy":
        # doc-worker-entrypoint.sh's real-subscription tasks don't route
        # through the GLM proxy at all (see that script's own header
        # comment) -- proxy health/canary/budget checks don't apply. Static
        # checks + circuit breaker above still fully apply and already ran.
        log_subscription_usage(task_dir_arg)
        ok("pre-flight checks passed (circuit-breaker, disk, memory, worktree) -- subscription-billed task, credit-accountant gate does not apply (2026-07-23 Owner directive), usage logged only")
    else:
        check_credit_accountant_approval(task_dir_arg)
        check_proxy_health(proxy_arg)
        canary_call(proxy_arg)
        ok("all pre-flight checks passed (circuit-breaker, disk, memory, worktree, proxy health, canary)")
