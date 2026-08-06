#!/usr/bin/env python3
"""generate_pm_report_v3.py -- real, pure, deterministic PM report generator.

UMR-20260805-181636-32f2 (parent UMR-20260802-165606-4413, OCID-020).
Standing directive this script exists to satisfy: UMR-20260806-042531-be9c (real deterministic/boolean/close-ended PM decisioning, zero manual AI/PM work -- see Sections 9-13, UMR-20260806-041307-0bfd).

Replaces the AI-reasoned-every-10-minutes PM report generator that was a
real contributing factor to hitting the Owner's weekly Claude Code usage
limit this session. This script makes ZERO AI/LLM calls of any kind -- every
field below is either a direct read of a real local source (a command, a
file, a SQL query) or a pure deterministic function of those reads. No
narration, no summarization-by-model, no judgment calls beyond fixed
threshold/rule logic documented in this file.

Real sources, one per header/status field:
  - /proc/meminfo directly (chosen over parsing `free -h`'s human-formatted
    columns -- same underlying kernel data, but /proc/meminfo's KEY: VALUE kB
    lines are trivial to parse exactly and do not depend on `free`'s locale/
    column-width formatting) for RAM/swap.
  - `systemctl --user list-timers veridian-cron-dispatch-tick.timer` for
    dispatch-tick cron/timer status.
  - `systemctl --user list-units 'veridian-worker@*' --state=running` for
    the live parallel worker count.
  - the real STUCK_TASKS_HEARTBEAT.json file, same default path
    dispatch-tick.py itself uses ({AI_OS}/STUCK_TASKS_HEARTBEAT.json,
    overridable via VERIDIAN_STUCK_TASKS_HEARTBEAT_PATH -- same env var name
    dispatch-tick.py reads, so a test/deploy override affects both
    consistently).
  - `tmux has-session -t claude` for direct CLI status.
  - resource_governor.py's own EMERGENCY_STOP_PATH constant, imported (not
    hardcoded) from the real script at SCRIPTS/resource_governor.py.
  - `PRAGMA integrity_check` against superboss-register.sqlite, via
    superboss-register.py's own _connect() (read-only query, no write lock
    needed). This database has one real, confirmed-corrupted table
    (file_inventory, held under Hard Rule 8 pending an Owner decision --
    see the real pm_decisions_pending row). A whole-database
    integrity_check WILL fail because of it. That is real, current, expected
    behavior -- db_integrity_ok below reflects it honestly; this script does
    not work around it, patch over it, or touch file_inventory in any way.
  - real SQL against gtm_certification_categories, ocid_canonical_registry
    and umr_tasks (all read-only SELECTs) for the OCID-020 GTM, test-results
    and deterministic-gate sections.

Established real precedent this script reuses rather than re-inventing:
  - The pass/fail/"blocked_or_pending" three-way split for
    gtm_certification_categories.passed (1 / 0 / NULL) is not this script's
    own invention -- it is exactly classify() in the already-real, already-
    merged gtm_check_production_readiness_audit.py (category_index=25,
    commit c9da808). That script treats "blocked" and "pending" as ONE
    combined state (a category that is neither confirmed-passing nor
    confirmed-failing), not two separately-tracked ones -- there is no DB
    column that distinguishes them. This script follows that same, already-
    established real definition instead of inventing a new blocked-vs-
    pending split with no real backing data to justify where the line falls.
  - "deterministic gate" (per this script's own required self-contained
    definition, since MASTER-TRACKER.yaml and OS.yaml do NOT exist anywhere
    on this server -- confirmed via search before writing this docstring):
    the only pre-existing "gate" concept for GTM/OCID-020 readiness found
    anywhere in this codebase is gtm_check_production_readiness_audit.py's
    own category_index=25 synthesis (a fixed P0/P1/P2/P3 severity rubric,
    documented in that script, over the other 24 categories' own already-
    computed passed values). This script's "deterministic gate" section
    reuses that category's own already-computed, already-real result
    (passed / evidence_summary / evidence_json) verbatim, rather than
    re-deriving a second, competing gate definition. It is "deterministic"
    in the sense this script cares about: a pure SQL read of an
    already-computed value, zero AI/LLM judgment applied here or in the
    category-25 script that computed it.

THE ONE PIECE THIS SCRIPT DOES NOT HAVE A REAL SOURCE FOR:
  The Go-To-Market readiness score's real bucket-mapping formula and the
  exact NOT_READY / LIMITED_PILOT / BETA / PRODUCTION threshold rule are
  defined by "Reporting Contract V3" SKILL.md, which normally lives at
  C:\\Users\\Dell\\.claude\\scheduled-tasks\\veridian-server-sentinel\\SKILL.md
  -- a Windows path that does NOT exist anywhere on this Linux server
  (confirmed via a full pruned search before this script was written). This
  script does NOT invent plausible-looking thresholds and present them as if
  copied from that real source. See compute_readiness_bucket() below: a
  clearly-labeled placeholder that always returns a fixed, conservative,
  explicitly-labeled non-answer. The report text also marks this with a
  "PLACEHOLDER" marker next to the recommendation line so it cannot be
  mistaken for a real computed score.

Threshold values chosen BY THIS TASK (not from SKILL.md, which is
unavailable -- see above) for the auto-generated open-issues section. These
are this script's own reasonable interpretation of the parent instruction's
"roughly ten percent" / "roughly twenty five" wording, and are declared here
as plain constants so they are easy to find, review, and revise once the
real SKILL.md becomes available:
  - SWAP_FREE_PCT_WARN_THRESHOLD = 10.0   -- swap free% strictly below this
    opens an issue. Chosen as the literal reading of "roughly ten percent".
  - LOAD_1MIN_WARN_THRESHOLD = 25.0       -- 1-minute load average strictly
    above this opens an issue. Chosen as the literal reading of "roughly
    twenty five" (this is a load-average NUMBER, not a percentage -- e.g. a
    1-minute load average of 25 on any core count is heavy load by any
    normal reading, which is why the 1-minute figure specifically was
    picked over 5/15-minute: it reacts fastest to a real current spike).

No AI/LLM calls anywhere in this file. Every open-issue, delta and status
line below is produced by plain Python conditionals over the real reads
above -- nothing here is a model call, a summarization, or a narrated
judgment.

Usage:
  python3 generate_pm_report_v3.py [--no-db-write] [--json-out PATH]

  --no-db-write   Skip the pm_report_snapshots INSERT and the two report
                  file writes (still runs and prints the full report to
                  stdout). Used by the test suite; also usable for a dry-run
                  on a live box.

-------------------------------------------------------------------------
SCRIPT_VERSION 3.1.0 (UMR-20260806-041307-0bfd, parent UMR-20260805-181636-32f2,
grandparent UMR-20260802-165606-4413/OCID-020) -- adds five new deterministic
sections (9-13 below) so the PM's own tokens go to judgment/dispatch/audit and
never to manual searching/checking, extending the same zero-AI-calls
discipline this whole file is built on. Every new section is, like the eight
above it, either a direct real read (a subprocess, a file, a SQL query) or a
pure function of those reads -- no new AI/LLM call anywhere.

  9.  DATABASE VALIDATION FOLD-IN -- shells out to the real, already-working
      `audit_ocid_compliance.py --report` (never touches its underlying
      audit logic directly), parses its real structured JSON stdout, folds a
      real pass/fail count + a real newly-failing-OCID list into this
      report. "Newly failing" is defined deterministically as: OCIDs with
      audit_passed=false in this run that were NOT already audit_passed=false
      in the immediately prior pm_report_snapshots row's own stored
      report_json (this script's own established prior-snapshot mechanism,
      see get_prior_snapshot() / Section 5). No prior row (or a prior row
      predating this section) means no real baseline exists yet --
      prior_baseline_available=False, newly_failing_ocids=[] honestly rather
      than a fabricated diff against nothing.
  10. 10-REPORT TREND ANALYSIS -- pure arithmetic over the real last (up to)
      10 pm_report_snapshots rows for swap_free_pct, load_1min and
      gtm_pass_count. Trend direction is computed by splitting the queried
      rows (oldest to newest) into a first half and a second half
      (values[:n//2] / values[n//2:] -- e.g. 10 rows -> 5 and 5; 7 rows -> 3
      and 4), averaging each half, and comparing:
        pct_change = (second_half_avg - first_half_avg) / abs(first_half_avg) * 100
        (0.0 when first_half_avg == 0 and second_half_avg == 0; treated as a
        real, non-zero directional change -- not "stable" -- when
        first_half_avg == 0 but second_half_avg != 0, since a % change is
        undefined there and calling it "stable" would hide a real move away
        from zero.)
      |pct_change| <= TREND_STABLE_TOLERANCE_PCT (5.0, this task's own
      documented choice, same spirit as SWAP_FREE_PCT_WARN_THRESHOLD above)
      => "stable". Otherwise the direction maps to improving/degrading per
      metric: swap_free_pct and gtm_pass_count are "higher is better" (an
      increase beyond tolerance is "improving"); load_1min is "lower is
      better" (an increase beyond tolerance is "degrading"). Fewer than 2 real
      rows for a given metric => "insufficient_data", never a fabricated
      trend. The real row count actually used is always reported honestly,
      including when it is under 10.
  11. DETERMINISTIC STALL DETECTION -- reuses the exact same
      STUCK_TASKS_HEARTBEAT.json read/parse this script's own Section 1
      (get_stuck_tasks(), now backed by the shared
      _load_stuck_tasks_heartbeat_doc() helper) already does, and the file's
      own real stuck_task_threshold_minutes value -- no new parsing
      invented. Folds one real line per real stuck task (task_id,
      blocked_minutes, last_note -- the heartbeat file's own real field
      names, confirmed by inspecting the live file before writing this,
      not guessed).
  12. DETERMINISTIC COLLISION DETECTION -- read-only, zero AI judgment on
      whether an overlap "really" is a collision, pure set-intersection.
      REDEFINED by UMR-20260806-043900-8c48 (real PM finding: the original
      file-overlap-only version flooded this section with ~12,800
      false-positive lines across the full historical PR backlog, pushing
      the whole report to 3.7MB/13,960 lines -- see
      COLLISION_FILE_OVERLAP_EXCLUDE_FILES's comment block above
      detect_pr_citation_collisions() for the full real finding). Current
      definition, two independent real sources:
        PRIMARY (the real signal -- every real collision this session
        actually looked like this, never a shared-file match): for every
        real open PR in every real tracked repo, AND for every real
        currently-running veridian-worker@*/veridian-supervisor@* unit,
        pairwise compares the set of UMR-YYYYMMDD-HHMMSS-xxxx ids AND
        task-YYYYMMDD-HHMMSS-<slug> task-identity tokens (regex,
        extract_citation_tokens()) found in that PR's real
        title+body+branch-name or that unit's real WorkingDirectory's
        prompt.txt, against every other PR/unit's set. No time/history
        scoping -- a same-citation match is real regardless of PR age.
        SECONDARY (kept, narrowed): real `gh pr diff <N> --name-only`
        changed-file-set pairwise overlap, but ONLY for PRs opened within
        the last COLLISION_FILE_OVERLAP_MAX_AGE_HOURS hours (48), and only
        for shared files outside the fixed, documented
        COLLISION_FILE_OVERLAP_EXCLUDE_FILES list (PROGRESS.md, common
        lockfiles/package.json, the shared ACTIVE-CLAIMS/CONSTITUTION
        YAMLs, the shared db schema file, tsconfig.json -- known common
        infrastructure churn that alone is never real collision signal).
        HARD CAP (defensive safety net, applies regardless of the above):
        if the combined candidate count exceeds COLLISION_CANDIDATE_CAP_TRIGGER
        (200), only the top COLLISION_TOP_K (50) candidates render
        (primary ranked before secondary, _rank_collision_candidates() --
        a fixed ordering rule, not AI judgment), plus an honest "N
        candidates found, showing top K" summary line.
      Emits COLLISION_DETECTED=YES/NO with the specific pair(s) named on
      YES. Tracked-repo list: this file found no single pre-existing
      canonical "tracked repos" constant to reuse -- resource_governor.py's
      own duplicate-PR guard (GH_PR_CHECK_REPOS) defaults to
      "compliance-tracker,projexa" and status-remediation-tick.py's
      TRACKED_REPOS is ["claude-control", "compliance-tracker", "projexa"]
      -- three different established scripts, three different real subsets,
      each serving that script's own distinct purpose. Rather than reusing
      either verbatim (neither is really "the" tracked-repo list for THIS
      section's purpose) or inventing a third unrelated set, this section
      uses COLLISION_TRACKED_REPOS = ("compliance-tracker", "veridian-scripts")
      -- the two repos this task's own directive named explicitly, both real,
      both env-overridable like every other path/constant in this file.
  13. DETERMINISTIC INSTRUCTION QUALITY CHECK -- queries the real last 20
      umr_tasks rows WHERE task_kind = 'veridian_task_create' (ORDER BY
      ts_submitted DESC) and applies one fixed rule to each row's real
      inputs_json.prompt text (rows with no string "prompt" key fail
      outright, real and documented, not silently skipped).
      task_kind filter (real fix, UMR-20260806-041307-0bfd, post-merge
      correction -- see PROGRESS.md): the original version of this section
      queried the raw, unfiltered last 20 umr_tasks rows. Confirmed live
      against this real database: at any given moment the umr_tasks table
      is dominated by a continuous stream of task_kind='systemctl_action'
      rows (dispatch-tick.py's own resume_interrupted_workers bookkeeping),
      which carry no 'prompt' field at all and are, by construction, always
      the most recent rows by ts_submitted. Left unfiltered, this section
      produced DETERMINISTIC_INSTRUCTION_COUNT=0/20 on every real run,
      permanently, regardless of actual dispatch-instruction quality --
      the opposite of a useful ten-minute PM signal. task_kind is a real,
      already-established umr_tasks column (see _ensure_umr_table() in
      superboss-register.py); 'veridian_task_create' is the real row type
      real Owner/PM dispatch prompts are written under (confirmed live
      against this database before this fix was written -- see
      submit()/veridian-task.py's own real write path).
        (a) a real UMR-YYYYMMDD-HHMMSS-xxxx citation is present anywhere
            (regex).
        (b) none of the fixed vague-verb substrings "look into", "improve",
            "consider", "explore" appear (case-insensitive).
        (c) a real concrete completion condition is present, defined as a
            purely structural, fixed regex check: either a file-path-looking
            token with a known extension (.py/.md/.yaml/.yml/.json/.sh/
            .sqlite[3]/.txt/.js/.ts/.toml/.cfg/.ini) OR a "PR #<N>" /
            "pull request #<N>" reference appears anywhere in the text. This
            is deliberately structural rather than a phrase-list, to avoid
            an arbitrary/biased "sounds concrete" judgment call.
      A row passes only if all three hold. Reports
      DETERMINISTIC_INSTRUCTION_COUNT=<pass_count>/<real_rows_checked> (the
      denominator is the real umr_tasks row count actually returned, honestly
      reported even when fewer than 20 real rows exist) plus every failing
      row's real umr_id and the specific rule(s) it failed.
  14. OWNER UMR CLOSURE TRACKING (UMR-20260806-070018-d88b item 4, extended
      by UMR-20260806-071942-5132) -- real SQL over umr_tasks rows with
      source_trigger='owner_dispatch_gateway' only, zero AI/LLM judgment:
        - ALL_TIME_TOTAL + a real status breakdown (COUNT(*) GROUP BY
          status).
        - OLDEST_OPEN_UMR: the real oldest (MIN ts_submitted) row whose
          status is still one of OWNER_UMR_OPEN_STATUSES (queued/dispatched/
          running -- umr_tasks' own CHECK constraint's non-terminal values),
          with its real age in hours. "none" (not fabricated) when no real
          row is currently open.
        - PERCENT_COMPLETE_24H_OWNER_UMR_SET: pure arithmetic over rows with
          ts_submitted in the trailing 24h -- (count of status in
          OWNER_UMR_CLOSED_STATUSES / real total rows in that window) * 100,
          rounded to 1 decimal, plus the full trailing-24h status breakdown.
          "insufficient_data" (not 0.0/100.0) when the window has zero real
          rows -- same honest-no-data spirit as Section 10.
-------------------------------------------------------------------------
"""
import argparse
import concurrent.futures
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Paths (all overridable via env vars, same convention as dispatch-tick.py /
# resource_governor.py, so tests can point every one of these at a temp file
# without touching the live server).
# ---------------------------------------------------------------------------
VERIDIAN_ROOT = os.environ.get("VERIDIAN_ROOT", "/opt/veridian")
AI_OS = os.environ.get("VERIDIAN_AI_OS_DIR", f"{VERIDIAN_ROOT}/ai-os")
SCRIPTS = os.environ.get("VERIDIAN_SCRIPTS_DIR", f"{VERIDIAN_ROOT}/scripts")

SBR_PATH = os.environ.get("VERIDIAN_SUPERBOSS_REGISTER_PY", f"{SCRIPTS}/superboss-register.py")
RESOURCE_GOVERNOR_PATH = os.environ.get(
    "VERIDIAN_RESOURCE_GOVERNOR_PY", f"{SCRIPTS}/resource_governor.py")

STUCK_TASKS_HEARTBEAT_PATH = os.environ.get(
    "VERIDIAN_STUCK_TASKS_HEARTBEAT_PATH", f"{AI_OS}/STUCK_TASKS_HEARTBEAT.json")

PROC_MEMINFO_PATH = os.environ.get("VERIDIAN_PM_REPORT_MEMINFO", "/proc/meminfo")
PROC_LOADAVG_PATH = os.environ.get("VERIDIAN_PM_REPORT_LOADAVG", "/proc/loadavg")

REPORT_LATEST_PATH = os.environ.get(
    "VERIDIAN_PM_REPORT_LATEST", f"{AI_OS}/reports/pm-report-latest.txt")
REPORT_HISTORY_PATH = os.environ.get(
    "VERIDIAN_PM_REPORT_HISTORY", f"{AI_OS}/reports/pm-report-history.log")

TMUX_SESSION_NAME = os.environ.get("VERIDIAN_PM_REPORT_TMUX_SESSION", "claude")
DISPATCH_TICK_TIMER_UNIT = "veridian-cron-dispatch-tick.timer"
WORKER_UNIT_GLOB = "veridian-worker@*"

# ---------------------------------------------------------------------------
# Open-issue thresholds -- this task's own documented interpretation. See
# the module docstring above for why these exact numbers were picked.
# ---------------------------------------------------------------------------
SWAP_FREE_PCT_WARN_THRESHOLD = 10.0
LOAD_1MIN_WARN_THRESHOLD = 25.0

REPORT_FORMAT_VERSION = "pm-report-v3-placeholder-gtm-score"

# UMR-20260806-041307-0bfd: this script's own version constant. Bumped on
# every real functional change; see the module docstring's SCRIPT_VERSION
# block for what changed at each bump.
# 3.1.1 (UMR-20260806-043900-8c48): narrowed Section 12's collision signal
# from raw historical file-overlap to primary UMR/task-identity citation
# match + narrowed/excluded-list secondary file-overlap + a hard output cap
# -- see the Section 12 comment block above detect_pr_citation_collisions().
# 3.1.2 (UMR-20260806-041307-0bfd): real fix, Section 13 (get_last_n_umr_tasks)
# -- added the missing task_kind='veridian_task_create' filter; see that
# function's own docstring for the real, confirmed-live bug this closes.
# 3.2.0 (real root-cause investigation, veridian-pm-report-tick.service +
# veridian-cron-file-inventory.service both hung ~1h+ and were SIGTERM-killed
# 2026-08-06 06:12/06:16): confirmed Section 12's secondary (file-overlap)
# signal was making its real `gh pr diff --name-only` calls SEQUENTIALLY --
# already O(n) real calls (one per recent PR, cached, PR#120 already fixed
# the O(n^2)/no-recency-scoping shape), but with no overall time budget, so
# at real live PR counts (94 combined recent-PR `gh` calls across both
# tracked repos at investigation time) a run of slow/rate-limited calls could
# still sum past an hour. Fix: (a) fetch each PR's changed-file set
# CONCURRENTLY, bounded by COLLISION_GH_MAX_WORKERS, instead of one at a
# time -- same real per-call gh invocations, same real 30s per-call timeout,
# just no longer serialized; (b) a real overall wall-clock deadline
# (COLLISION_SECTION_TIME_BUDGET_SECONDS) on the whole collision section, so
# a report run degrades to an honest "N PRs skipped, time budget exceeded"
# rather than blocking the whole 10-minute report tick for an hour. The
# PRIMARY citation-match signal and the exclude-list from UMR-20260806-043900-
# 8c48 are unchanged -- this is a wall-clock/parallelism fix layered on top,
# not a redo of that accuracy fix. Also adds Section 14 (UMR-20260806-070018-
# d88b item 4, extended by UMR-20260806-071942-5132): deterministic UMR
# closure tracking for source_trigger='owner_dispatch_gateway' rows.
SCRIPT_VERSION = "3.2.0"

# ---------------------------------------------------------------------------
# Section 9-13 constants (UMR-20260806-041307-0bfd) -- see module docstring
# for full documentation of what each of these controls and why this exact
# value was chosen.
# ---------------------------------------------------------------------------
AUDIT_OCID_COMPLIANCE_PY_PATH = os.environ.get(
    "VERIDIAN_AUDIT_OCID_COMPLIANCE_PY", f"{SCRIPTS}/audit_ocid_compliance.py")

TREND_WINDOW_SIZE = 10
TREND_STABLE_TOLERANCE_PCT = 5.0
TREND_METRICS = ("swap_free_pct", "load_1min", "gtm_pass_count")
# "higher is better" metrics: an increase beyond tolerance is "improving".
# Any metric not listed here is treated as "lower is better" (load_1min).
TREND_HIGHER_IS_BETTER = {"swap_free_pct", "gtm_pass_count"}

GH_ORG = os.environ.get("VERIDIAN_GH_ORG", "FChecklist")
COLLISION_TRACKED_REPOS = tuple(
    r.strip() for r in os.environ.get(
        "VERIDIAN_PM_REPORT_COLLISION_REPOS", "compliance-tracker,veridian-scripts"
    ).split(",") if r.strip()
)
WORKER_COLLISION_UNIT_GLOBS = ("veridian-worker@*", "veridian-supervisor@*")

UMR_CITATION_RE = re.compile(r"UMR-\d{8}-\d{6}-[0-9a-f]{4}")
VAGUE_VERB_PHRASES = ("look into", "improve", "consider", "explore")

# UMR-20260806-043900-8c48: narrows Section 12's collision signal. See the
# module docstring's SCRIPT_VERSION block and the Section 12 comment above
# detect_pr_citation_collisions() for the full real finding/fix.
TASK_IDENTITY_RE = re.compile(r"\btask-\d{8}-\d{6}-[a-z0-9][a-z0-9-]*")
COLLISION_FILE_OVERLAP_MAX_AGE_HOURS = 48
COLLISION_FILE_OVERLAP_EXCLUDE_FILES = frozenset({
    "PROGRESS.md",
    "package.json",
    "package-lock.json",
    "bun.lock",
    "yarn.lock",
    "pnpm-lock.yaml",
    "ai-os/boss/ACTIVE-CLAIMS.yaml",
    "ai-os/CONSTITUTION.yaml",
    "src/lib/db/schema.ts",
    "tsconfig.json",
})
COLLISION_CANDIDATE_CAP_TRIGGER = 200
COLLISION_TOP_K = 50

# Real perf fix (see SCRIPT_VERSION 3.2.0 note above): bounded parallelism
# for the real `gh pr diff` calls, plus an overall wall-clock deadline for
# the whole collision-detection section so a slow/rate-limited run of gh
# calls can never again block the full report for an hour.
COLLISION_GH_MAX_WORKERS = int(os.environ.get("VERIDIAN_PM_REPORT_COLLISION_MAX_WORKERS", "8"))
COLLISION_SECTION_TIME_BUDGET_SECONDS = float(
    os.environ.get("VERIDIAN_PM_REPORT_COLLISION_TIME_BUDGET_SECONDS", "120"))

# Section 14 (UMR-20260806-070018-d88b item 4, extended by
# UMR-20260806-071942-5132): real UMR closure tracking, scoped to
# source_trigger='owner_dispatch_gateway' rows only -- the real Owner-
# dispatch channel this section exists to make visible.
OWNER_DISPATCH_SOURCE_TRIGGER = "owner_dispatch_gateway"
# The umr_tasks CHECK constraint's own non-terminal values (see
# superboss-register.py schema) -- rows in one of these statuses are real,
# still "open" (not yet resolved either way).
OWNER_UMR_OPEN_STATUSES = frozenset({"queued", "dispatched", "running"})
# UMR-20260806-071942-5132's own literal status set for the "closed" side of
# PERCENT_COMPLETE_24H_OWNER_UMR_SET. Documented honestly: umr_tasks.status
# has a real CHECK(status IN ('queued','dispatched','running','completed',
# 'failed','rejected_duplicate','sigterm_sent','killed')) constraint (see
# superboss-register.py schema) -- 'merged'/'verified'/'closed' are not real
# reachable values in the live schema today, so in practice only 'completed'
# rows ever count toward the numerator. Kept as the exact set the directive
# named (harmless supersets never match, real if the schema ever adds them)
# rather than silently narrowing it to 'completed' alone.
OWNER_UMR_CLOSED_STATUSES = frozenset({"completed", "merged", "verified", "closed"})
CONCRETE_COMPLETION_FILE_PATH_RE = re.compile(
    r"\b[\w][\w\-./]*\.(?:py|md|ya?ml|json|sh|sqlite3?|txt|js|ts|toml|cfg|ini)\b",
    re.IGNORECASE,
)
CONCRETE_COMPLETION_PR_REF_RE = re.compile(
    r"\bPR\s*#?\d+\b|\bpull request\s*#?\d+\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_module_from_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_cmd(argv, timeout=30):
    """Runs a real subprocess and returns (returncode, stdout, stderr).
    Never raises on a non-zero exit or missing binary -- callers decide what
    a given exit code/output means; this just captures it faithfully."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError as e:
        return 127, "", str(e)
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout or "", str(e)


# ---------------------------------------------------------------------------
# Section 1: header/status, real local sources
# ---------------------------------------------------------------------------
def parse_meminfo(text):
    """Pure function: parses /proc/meminfo text (KEY: VALUE kB lines) into
    the fields this report needs. Kept separate from the file read so tests
    can feed synthetic text without a real /proc/meminfo."""
    vals = {}
    for line in text.splitlines():
        m = re.match(r"^(\w+):\s+(\d+)\s*kB\s*$", line)
        if m:
            vals[m.group(1)] = int(m.group(2))
    mem_total_kb = vals.get("MemTotal")
    mem_available_kb = vals.get("MemAvailable")
    swap_total_kb = vals.get("SwapTotal")
    swap_free_kb = vals.get("SwapFree")

    result = {
        "mem_total_mb": (mem_total_kb // 1024) if mem_total_kb is not None else None,
        "mem_available_mb": (mem_available_kb // 1024) if mem_available_kb is not None else None,
        "swap_total_mb": (swap_total_kb // 1024) if swap_total_kb is not None else None,
        "swap_free_mb": (swap_free_kb // 1024) if swap_free_kb is not None else None,
        "swap_free_pct": None,
    }
    if swap_total_kb is not None and swap_free_kb is not None and swap_total_kb > 0:
        result["swap_free_pct"] = round((swap_free_kb / swap_total_kb) * 100.0, 2)
    elif swap_total_kb == 0:
        # No swap configured at all: nothing to run low on. Report 100.0 so
        # the "swap free% < threshold" open-issue rule below never fires on
        # a box that simply has no swap.
        result["swap_free_pct"] = 100.0
    return result


def get_ram_swap():
    try:
        with open(PROC_MEMINFO_PATH) as f:
            text = f.read()
        return parse_meminfo(text)
    except OSError as e:
        return {"error": f"could not read {PROC_MEMINFO_PATH}: {e}"}


def parse_loadavg(text):
    parts = text.split()
    if len(parts) < 3:
        return {"error": f"unexpected /proc/loadavg format: {text!r}"}
    return {
        "load_1min": float(parts[0]),
        "load_5min": float(parts[1]),
        "load_15min": float(parts[2]),
    }


def get_load_average():
    try:
        with open(PROC_LOADAVG_PATH) as f:
            text = f.read()
        return parse_loadavg(text)
    except OSError as e:
        return {"error": f"could not read {PROC_LOADAVG_PATH}: {e}"}


def parse_dispatch_tick_timer_active(stdout, unit_name):
    """Pure function: `systemctl --user list-timers <unit>` prints a table
    with the unit name in it when the timer is loaded/scheduled, and prints
    only the header + "0 timers listed." when it is not. Deterministic
    substring check against the exact unit name -- no fuzzy matching."""
    return unit_name in stdout


def get_dispatch_tick_status():
    code, out, err = run_cmd(["systemctl", "--user", "list-timers", DISPATCH_TICK_TIMER_UNIT])
    active = code == 0 and parse_dispatch_tick_timer_active(out, DISPATCH_TICK_TIMER_UNIT)
    return {
        "dispatch_tick_active": bool(active),
        "raw_exit_code": code,
        "raw_stdout": out.strip(),
        "raw_stderr": err.strip(),
    }


def parse_worker_count(stdout):
    """Pure function: counts real 'loaded units listed' from
    `systemctl --user list-units 'veridian-worker@*' --state=running`
    output. Falls back to counting UNIT lines starting with the worker
    prefix if the summary line's wording ever changes, so a parsing edge
    case degrades to a still-correct count rather than a crash."""
    m = re.search(r"^(\d+) loaded units listed\.", stdout, re.MULTILINE)
    if m:
        return int(m.group(1))
    return len(re.findall(r"^\s*veridian-worker@\S+\.service\s", stdout, re.MULTILINE))


def get_worker_count():
    code, out, err = run_cmd(["systemctl", "--user", "list-units", WORKER_UNIT_GLOB, "--state=running"])
    if code != 0:
        return {"parallel_worker_count": None, "error": err.strip() or f"exit {code}"}
    return {"parallel_worker_count": parse_worker_count(out), "raw_stdout": out.strip()}


def _load_stuck_tasks_heartbeat_doc():
    """Shared real read/parse of STUCK_TASKS_HEARTBEAT.json -- the ONE place
    this file opens/parses that JSON file. get_stuck_tasks() (Section 1
    summary) and get_stuck_tasks_detail() (Section 11, UMR-20260806-041307-0bfd)
    both call this rather than each re-opening the file, per this task's own
    "reuse the same parsing, don't reinvent" instruction. Returns
    (doc_or_None, error_or_None)."""
    try:
        with open(STUCK_TASKS_HEARTBEAT_PATH) as f:
            return json.load(f), None
    except (OSError, json.JSONDecodeError) as e:
        return None, f"could not read/parse {STUCK_TASKS_HEARTBEAT_PATH}: {e}"


def get_stuck_tasks():
    doc, err = _load_stuck_tasks_heartbeat_doc()
    if err:
        return {"stuck_task_count": None, "error": err}
    stuck = doc.get("stuck_tasks")
    return {
        "stuck_task_count": len(stuck) if isinstance(stuck, list) else None,
        "heartbeat_generated_at": doc.get("generated_at"),
        "stuck_task_threshold_minutes": doc.get("stuck_task_threshold_minutes"),
        "real_task_counts": doc.get("real_task_counts"),
    }


def get_stuck_tasks_detail():
    """Section 11 (UMR-20260806-041307-0bfd) data source. Reuses
    _load_stuck_tasks_heartbeat_doc() (same parsing as get_stuck_tasks()
    above) and pulls out one real line's worth of fields per real stuck
    task -- task_id, blocked_minutes, last_note -- using the heartbeat
    file's own real field names (confirmed by inspecting the live file
    before writing this function, not guessed)."""
    doc, err = _load_stuck_tasks_heartbeat_doc()
    if err:
        return {"error": err, "stuck_task_threshold_minutes": None, "tasks": []}
    stuck = doc.get("stuck_tasks")
    tasks = []
    if isinstance(stuck, list):
        for t in stuck:
            if isinstance(t, dict):
                tasks.append({
                    "task_id": t.get("task_id"),
                    "blocked_minutes": t.get("blocked_minutes"),
                    "last_note": t.get("last_note"),
                })
    return {
        "error": None,
        "stuck_task_threshold_minutes": doc.get("stuck_task_threshold_minutes"),
        "tasks": tasks,
    }


def get_tmux_status():
    code, out, err = run_cmd(["tmux", "has-session", "-t", TMUX_SESSION_NAME])
    return {"tmux_session_alive": code == 0, "raw_exit_code": code}


def get_emergency_stop():
    try:
        governor = load_module_from_path("resource_governor", RESOURCE_GOVERNOR_PATH)
        path = governor.EMERGENCY_STOP_PATH
    except Exception as e:
        return {"emergency_stop_present": None, "error": f"could not import {RESOURCE_GOVERNOR_PATH}: {e}"}
    return {"emergency_stop_present": os.path.exists(path), "emergency_stop_path": path}


def get_db_integrity(sbr):
    """PRAGMA integrity_check against the whole DB, via superboss-register.py's
    own _connect(). Read-only -- no _write_lock() needed. Real, expected
    current behavior: this FAILS (returns more than just the single row
    ['ok']) because of the confirmed-corrupted file_inventory table, held
    under Hard Rule 8. This function does not touch file_inventory, does not
    special-case it out of the result, and does not retry/paper over the
    failure -- it reports exactly what PRAGMA integrity_check said."""
    try:
        conn = sbr._connect()
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        conn.close()
    except sqlite3.Error as e:
        return {"db_integrity_ok": False, "integrity_check_rows": [], "error": str(e)}
    values = [r[0] for r in rows]
    ok = values == ["ok"]
    return {"db_integrity_ok": ok, "integrity_check_rows": values}


# ---------------------------------------------------------------------------
# Section 2/3: OCID-020 GTM, test results, deterministic gate
# ---------------------------------------------------------------------------
def classify_passed(passed):
    """Same three-way classification as the real, already-merged
    gtm_check_production_readiness_audit.py's classify() -- reused
    verbatim, not re-derived, per this script's own docstring."""
    if passed == 1:
        return "pass"
    if passed == 0:
        return "fail"
    return "blocked_or_pending"


def get_gtm_section(sbr):
    try:
        conn = sbr._connect()
        rows = conn.execute(
            "SELECT category_index, category_name, ocid_number, passed, "
            "evidence_summary, validated_at FROM gtm_certification_categories "
            "ORDER BY category_index"
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return {"error": str(e)}

    categories = []
    counts = {"pass": 0, "fail": 0, "blocked_or_pending": 0}
    for r in rows:
        state = classify_passed(r["passed"])
        counts[state] += 1
        categories.append({
            "category_index": r["category_index"],
            "category_name": r["category_name"],
            "ocid_number": r["ocid_number"],
            "passed": r["passed"],
            "state": state,
            "evidence_summary": r["evidence_summary"],
            "validated_at": r["validated_at"],
        })

    gate_row = next((c for c in categories if c["category_index"] == 25), None)

    return {
        "gtm_pass_count": counts["pass"],
        "gtm_fail_count": counts["fail"],
        "gtm_blocked_or_pending_count": counts["blocked_or_pending"],
        "categories": categories,
        "deterministic_gate": {
            "definition": (
                "Reuses gtm_certification_categories.category_index=25's own "
                "already-computed result (gtm_check_production_readiness_audit.py's "
                "P0/P1/P2/P3 severity-rubric synthesis over the other 24 categories). "
                "MASTER-TRACKER.yaml and OS.yaml do not exist on this server "
                "(confirmed via search) so no other established 'deterministic gate' "
                "definition could be found to reuse instead."
            ),
            "category_25_row": gate_row,
            "gate_result": (gate_row["state"] if gate_row else "UNKNOWN (category 25 row not found)"),
        },
    }


def get_ocid_registry_section(sbr):
    try:
        conn = sbr._connect()
        total = conn.execute("SELECT COUNT(*) FROM ocid_canonical_registry").fetchone()[0]
        status_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM ocid_canonical_registry GROUP BY status ORDER BY status"
        ).fetchall()
        complete = conn.execute(
            "SELECT COUNT(*) FROM ocid_canonical_registry WHERE is_fully_complete = 1"
        ).fetchone()[0]
        conn.close()
    except sqlite3.Error as e:
        return {"error": str(e)}
    return {
        "ocid_canonical_registry_total": total,
        "ocid_canonical_registry_fully_complete": complete,
        "ocid_canonical_registry_by_status": {r["status"]: r["n"] for r in status_rows},
    }


def get_umr_tasks_section(sbr):
    try:
        conn = sbr._connect()
        total = conn.execute("SELECT COUNT(*) FROM umr_tasks").fetchone()[0]
        status_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM umr_tasks GROUP BY status ORDER BY status"
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return {"error": str(e)}
    return {
        "umr_tasks_total": total,
        "umr_tasks_by_status": {r["status"]: r["n"] for r in status_rows},
    }


# ---------------------------------------------------------------------------
# Section 4: GTM readiness score placeholder -- SEE MODULE DOCSTRING.
# ---------------------------------------------------------------------------
def compute_readiness_bucket(gtm_section):
    """PLACEHOLDER -- NOT a real computed GTM readiness bucket.

    TODO(SKILL.md section 8, not available on this server): the real
    bucket-mapping formula and the exact NOT_READY / LIMITED_PILOT / BETA /
    PRODUCTION threshold rule live in "Reporting Contract V3" SKILL.md at
    C:\\Users\\Dell\\.claude\\scheduled-tasks\\veridian-server-sentinel\\SKILL.md
    (a Windows path). That file does not exist anywhere on this Linux
    server (confirmed via a full pruned search before this script was
    written). Nobody has copied its real section-8 formula/thresholds onto
    this box. DO NOT replace this function with invented-but-plausible-
    looking numbers/thresholds and present them as if they came from that
    real source -- that would be worse than this honest placeholder.

    This function deliberately ignores gtm_section's real counts (even
    though they are passed in, so a future real implementation has them
    ready to use) and always returns the same fixed, conservative,
    explicitly-labeled non-answer.
    """
    del gtm_section  # unused on purpose -- see docstring
    return {
        "bucket": "NOT_READY -- placeholder, real thresholds pending SKILL.md",
        "is_placeholder": True,
        "reason": (
            "compute_readiness_bucket() has no real source for the bucket-mapping "
            "formula/thresholds on this server (SKILL.md unavailable -- see "
            "module docstring and this function's own TODO). Returns a fixed "
            "conservative placeholder rather than an invented score."
        ),
    }


# ---------------------------------------------------------------------------
# Section 5: implementation summary (real deltas vs prior snapshot row)
# ---------------------------------------------------------------------------
DELTA_FIELDS = [
    "gtm_pass_count", "gtm_fail_count", "gtm_blocked_count", "gtm_pending_count",
    "mem_available_mb", "swap_free_pct", "load_1min", "load_5min", "load_15min",
    "dispatch_tick_active", "parallel_worker_count", "stuck_task_count",
    "tmux_session_alive", "emergency_stop_present", "db_integrity_ok",
    "umr_tasks_total", "ocid_canonical_registry_total",
]


def get_prior_snapshot(sbr):
    try:
        conn = sbr._connect()
        row = conn.execute(
            "SELECT * FROM pm_report_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
    except sqlite3.Error as e:
        return None, str(e)
    if row is None:
        return None, None
    return dict(row), None


def compute_deltas(prior, current):
    """Pure function: real +N / -N / unchanged / new (no prior row) per
    field. No narration -- just arithmetic and string formatting."""
    deltas = {}
    for field in DELTA_FIELDS:
        cur_val = current.get(field)
        if prior is None:
            deltas[field] = "new (no prior pm_report_snapshots row)"
            continue
        prior_val = prior.get(field)
        if prior_val is None or cur_val is None:
            deltas[field] = f"unknown (prior={prior_val!r} current={cur_val!r})"
            continue
        if isinstance(cur_val, bool) or isinstance(prior_val, bool):
            cur_b, prior_b = bool(cur_val), bool(prior_val)
            deltas[field] = "unchanged" if cur_b == prior_b else f"{prior_b} -> {cur_b}"
            continue
        try:
            diff = cur_val - prior_val
        except TypeError:
            deltas[field] = "unchanged" if cur_val == prior_val else f"{prior_val!r} -> {cur_val!r}"
            continue
        if diff == 0:
            deltas[field] = "unchanged"
        elif diff > 0:
            deltas[field] = f"+{diff}" if isinstance(diff, int) else f"+{diff:.2f}"
        else:
            deltas[field] = f"{diff}" if isinstance(diff, int) else f"{diff:.2f}"
    return deltas


# ---------------------------------------------------------------------------
# Section 6: open issues -- pure threshold/rule logic, zero AI judgment
# ---------------------------------------------------------------------------
def build_open_issues(gtm_section, db_integrity, ram_swap, load_avg):
    issues = []

    for cat in gtm_section.get("categories", []):
        if cat["state"] == "fail":
            issues.append({
                "kind": "gtm_category_failed",
                "category_index": cat["category_index"],
                "category_name": cat["category_name"],
                "ocid_number": cat["ocid_number"],
                "root_cause": cat["evidence_summary"],
            })

    if db_integrity.get("db_integrity_ok") is False:
        issues.append({
            "kind": "db_integrity_check_failed",
            "detail": (
                "PRAGMA integrity_check against superboss-register.sqlite did not "
                "return exactly ['ok']. This is the real, expected, current state "
                "(1 confirmed-corrupted table, file_inventory, held under Hard Rule "
                "8 pending an Owner decision -- see PM decisions pending section)."
            ),
            "integrity_check_rows": db_integrity.get("integrity_check_rows")
                or ([db_integrity["error"]] if db_integrity.get("error") else []),
        })

    swap_pct = ram_swap.get("swap_free_pct")
    if isinstance(swap_pct, (int, float)) and swap_pct < SWAP_FREE_PCT_WARN_THRESHOLD:
        issues.append({
            "kind": "swap_free_low",
            "detail": (
                f"swap_free_pct={swap_pct:.2f}% is below the "
                f"{SWAP_FREE_PCT_WARN_THRESHOLD}% threshold (see module docstring "
                "for why this exact number was chosen)."
            ),
        })

    load1 = load_avg.get("load_1min")
    if isinstance(load1, (int, float)) and load1 > LOAD_1MIN_WARN_THRESHOLD:
        issues.append({
            "kind": "load_average_high",
            "detail": (
                f"load_1min={load1} exceeds the {LOAD_1MIN_WARN_THRESHOLD} threshold "
                "(see module docstring for why this exact number was chosen)."
            ),
        })

    return issues


# ---------------------------------------------------------------------------
# Section 7: PM decisions pending -- read-only, verbatim
# ---------------------------------------------------------------------------
def _pm_decisions_pending_has_decision_type(conn):
    """True once pm_decisions_pending carries the Owner standing-mandate
    decision_type column (task-20260806-034817, cites
    UMR-20260805-185000-e94f -- see superboss-register.py's
    _migrate_pm_decisions_pending_owner_proposal_columns() for the write
    side). Checked live via PRAGMA table_info rather than assumed, so this
    read-only script degrades gracefully (never raises) against a DB that
    predates that migration -- same defensive spirit as this script's own
    db_integrity check never assuming a clean database."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(pm_decisions_pending)").fetchall()}
    return "decision_type" in cols


def get_pm_decisions_pending(sbr):
    try:
        conn = sbr._connect()
        # Once decision_type exists, exclude 'owner_proposal' rows -- those
        # surface separately in get_owner_proposals_pending() (Section 8)
        # below, same table, same 'status=open means awaiting a decision'
        # convention, but a distinct real workflow the Owner's standing
        # mandate keeps visually separate in the report. On a DB that
        # predates decision_type, no filter is applied (there are no
        # owner_proposal rows to exclude yet), preserving this function's
        # original behavior exactly.
        type_filter = " AND decision_type = 'pm_decision'" if _pm_decisions_pending_has_decision_type(conn) else ""
        rows = conn.execute(
            "SELECT id, opened_ts, title, detail, options_json, recommended_option, "
            f"related_umr, status FROM pm_decisions_pending WHERE status = 'open'{type_filter} "
            "ORDER BY id"
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return {"error": str(e)}
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Section 8: Owner/AI child-UMR proposals pending -- read-only, verbatim
#
# Owner standing mandate (task-20260806-034817, cites
# UMR-20260805-185000-e94f): "thinking is by the Project Manager, execution
# is by AI agents" for real novel findings outside already-approved scope.
# Same real table (pm_decisions_pending), same read-only-section pattern as
# Section 7 above -- this is the report-side half of
# superboss-register.py's insert_owner_proposal()/decide_owner_proposal()/
# record_owner_proposal_completion(), so the PM sees real pending proposals
# every real report cycle without a separate real query of their own.
# ---------------------------------------------------------------------------
def get_owner_proposals_pending(sbr):
    try:
        conn = sbr._connect()
        if not _pm_decisions_pending_has_decision_type(conn):
            conn.close()
            return []  # DB predates the Owner-proposal columns -- no real rows can exist yet
        rows = conn.execute(
            "SELECT id, opened_ts, title AS issue, detail AS proposal, related_umr AS child_umr, "
            "status FROM pm_decisions_pending WHERE status = 'open' AND decision_type = 'owner_proposal' "
            "ORDER BY id"
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return {"error": str(e)}
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Section 9 (UMR-20260806-041307-0bfd): database validation fold-in --
# shells out to the real, already-working audit_ocid_compliance.py --report,
# never invokes the underlying audit logic directly. See module docstring
# for the "newly failing" definition.
# ---------------------------------------------------------------------------
def _prior_failing_ocids(prior_snapshot_row):
    """Pure function: pulls the prior run's own failing_ocids list back out
    of pm_report_snapshots.report_json (this script's own established
    full-report-JSON storage, see write_snapshot_row()). Returns
    (set_of_ocid_numbers, baseline_available_bool). No prior row, no
    report_json, unparseable JSON, or a report_json predating this section
    -> ({}, False), never a fabricated baseline."""
    if not prior_snapshot_row:
        return set(), False
    raw = prior_snapshot_row.get("report_json")
    if not raw:
        return set(), False
    try:
        prior_report = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return set(), False
    section = prior_report.get("ocid_compliance_audit_section")
    if not isinstance(section, dict) or "failing_ocids" not in section:
        return set(), False
    return set(section.get("failing_ocids") or []), True


def get_ocid_compliance_audit_section(prior_snapshot_row):
    code, out, err = run_cmd(["python3", AUDIT_OCID_COMPLIANCE_PY_PATH, "--report"], timeout=60)
    if code != 0:
        return {"error": err.strip() or f"exit {code}", "raw_stdout": out.strip()[:2000]}
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        return {"error": f"could not parse audit_ocid_compliance.py --report JSON: {e}",
                "raw_stdout": out.strip()[:2000]}

    rows = data.get("rows", [])
    passed_count = sum(1 for r in rows if r.get("audit_passed") is True)
    failed_count = sum(1 for r in rows if r.get("audit_passed") is False)
    current_fail_set = {r.get("ocid_number") for r in rows if r.get("audit_passed") is False}
    prior_fail_set, baseline_available = _prior_failing_ocids(prior_snapshot_row)
    newly_failing = sorted(current_fail_set - prior_fail_set) if baseline_available else []

    return {
        "subcommand": ["python3", AUDIT_OCID_COMPLIANCE_PY_PATH, "--report"],
        "mode": data.get("mode"),
        "read_only": data.get("read_only"),
        "total_rows": len(rows),
        "audit_passed_count": passed_count,
        "audit_failed_count": failed_count,
        "failing_ocids": sorted(o for o in current_fail_set if o is not None),
        "prior_baseline_available": baseline_available,
        "newly_failing_ocids": newly_failing,
    }


# ---------------------------------------------------------------------------
# Section 10 (UMR-20260806-041307-0bfd): 10-report trend analysis -- pure
# arithmetic over the real last (up to) TREND_WINDOW_SIZE pm_report_snapshots
# rows. See module docstring for the exact first-half/second-half/tolerance
# definition of improving/degrading/stable.
# ---------------------------------------------------------------------------
def get_recent_snapshots(sbr, limit=TREND_WINDOW_SIZE):
    try:
        conn = sbr._connect()
        rows = conn.execute(
            "SELECT * FROM pm_report_snapshots ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return None, str(e)
    # Reverse to chronological (oldest-first) order -- first-half/second-half
    # split below is meaningless unless the rows are time-ordered.
    return [dict(r) for r in reversed(rows)], None


def compute_trend_for_series(values):
    """Pure function: real first-half-average vs second-half-average
    comparison. `values` must already be in chronological (oldest-first)
    order with None entries removed by the caller."""
    n = len(values)
    if n < 2:
        return {"trend": "insufficient_data", "rows_used": n}
    half = n // 2
    first_half = values[:half]
    second_half = values[half:]
    first_avg = sum(first_half) / len(first_half)
    second_avg = sum(second_half) / len(second_half)
    if first_avg == 0:
        pct_change = 0.0 if second_avg == 0 else float("inf") if second_avg > 0 else float("-inf")
    else:
        pct_change = (second_avg - first_avg) / abs(first_avg) * 100.0

    if first_avg == 0 and second_avg == 0:
        trend = "stable"
    elif abs(pct_change) <= TREND_STABLE_TOLERANCE_PCT:
        trend = "stable"
    else:
        trend = "up" if pct_change > 0 else "down"
    return {
        "trend_raw_direction": trend,
        "rows_used": n,
        "first_half_avg": round(first_avg, 4),
        "second_half_avg": round(second_avg, 4),
        "pct_change": (round(pct_change, 2) if pct_change not in (float("inf"), float("-inf")) else pct_change),
    }


def _apply_metric_semantics(metric, raw_result):
    if raw_result.get("trend_raw_direction") is None:
        return raw_result  # insufficient_data, nothing to map
    direction = raw_result.pop("trend_raw_direction")
    if direction == "stable":
        trend = "stable"
    else:
        higher_is_better = metric in TREND_HIGHER_IS_BETTER
        going_up = direction == "up"
        trend = ("improving" if going_up else "degrading") if higher_is_better \
            else ("degrading" if going_up else "improving")
    raw_result["trend"] = trend
    return raw_result


def get_trend_analysis(sbr):
    snapshots, err = get_recent_snapshots(sbr, TREND_WINDOW_SIZE)
    if err:
        return {"error": err, "rows_used": 0, "metrics": {}}
    if not snapshots:
        return {"error": None, "rows_used": 0, "note": "no pm_report_snapshots rows exist yet", "metrics": {}}

    metrics = {}
    for metric in TREND_METRICS:
        values = [s[metric] for s in snapshots if s.get(metric) is not None]
        result = compute_trend_for_series(values)
        metrics[metric] = _apply_metric_semantics(metric, result)

    return {
        "error": None,
        "rows_used": len(snapshots),
        "window_size_requested": TREND_WINDOW_SIZE,
        "stable_tolerance_pct": TREND_STABLE_TOLERANCE_PCT,
        "metrics": metrics,
    }


# ---------------------------------------------------------------------------
# Section 12 (UMR-20260806-041307-0bfd, narrowed by UMR-20260806-043900-8c48):
# deterministic collision detection.
#
# UMR-20260806-043900-8c48 (real PM finding, confirmed live in umr_tasks):
# the original version's only signal was raw file-overlap across the FULL
# open-PR backlog, no exclude list, no recency scoping -- it flooded this
# section with ~12,800 false-positive lines (unrelated PRs that merely both
# touched a normal, weekly-churned shared file like PROGRESS.md/
# package.json), pushing the whole report to 3.7MB/13,960 lines. This
# session's own REAL collisions (PR #98/#100, #102/#103, #110/#111,
# #115/#116) were never a shared-file match -- every one was two PRs/workers
# citing the SAME UMR ID or task-identity. Redefined:
#
#   PRIMARY signal (the real one, see detect_pr_citation_collisions() /
#   detect_worker_umr_collisions()): two open PRs, or two running
#   veridian-worker@*/veridian-supervisor@* units, whose combined
#   title+body+branch-name (PRs) or prompt.txt (workers) cite the same real
#   UMR-YYYYMMDD-HHMMSS-xxxx id or the same real task-YYYYMMDD-HHMMSS-<slug>
#   task-identity token (extract_citation_tokens()). No time/repo-history
#   scoping -- a same-UMR citation is real signal regardless of PR age.
#
#   SECONDARY signal (file overlap, see detect_pr_file_collisions()): kept,
#   but only for PRs opened within the last
#   COLLISION_FILE_OVERLAP_MAX_AGE_HOURS hours (48 -- recent PRs, not the
#   full historical backlog), and only after excluding
#   COLLISION_FILE_OVERLAP_EXCLUDE_FILES (known common infrastructure files
#   that alone are never real collision signal: PROGRESS.md/lockfiles/
#   package.json/the shared claims+constitution YAMLs/db schema/tsconfig --
#   this task's own found list plus what UMR-20260806-043900-8c48 itself
#   named).
#
#   HARD CAP (defensive, applies regardless of the above -- see
#   _cap_collision_candidates()): if the combined candidate count exceeds
#   COLLISION_CANDIDATE_CAP_TRIGGER (200), only the top COLLISION_TOP_K (50)
#   candidates are rendered, primary-signal candidates ranked before
#   secondary/file-overlap ones (_rank_collision_candidates() -- a fixed,
#   documented ordering rule, not AI judgment), plus an honest "N candidates
#   found, showing top K" summary line. Never an unbounded dump.
# ---------------------------------------------------------------------------
def extract_citation_tokens(text):
    """Pure function: the PRIMARY collision signal
    (UMR-20260806-043900-8c48). Returns the set of real
    UMR-YYYYMMDD-HHMMSS-xxxx ids AND real task-YYYYMMDD-HHMMSS-<slug>
    task-identity tokens found in `text`. Two PRs/workers sharing ANY token
    from this set are citing the same real directive -- this, not a shared
    file, is what every real collision this session actually looked like."""
    tokens = set(UMR_CITATION_RE.findall(text or ""))
    tokens |= set(TASK_IDENTITY_RE.findall(text or ""))
    return tokens


def extract_umr_ids(text):
    """Pure function over real text -- no fuzzy matching, exact
    UMR-YYYYMMDD-HHMMSS-xxxx pattern only. Narrower than
    extract_citation_tokens() (UMR ids only, no task-identity tokens); kept
    as its own function since some callers/tests only care about UMR ids
    specifically."""
    return set(UMR_CITATION_RE.findall(text or ""))


def get_open_pr_list(repo):
    code, out, err = run_cmd(
        ["gh", "pr", "list", "--repo", f"{GH_ORG}/{repo}", "--state", "open",
         "--json", "number,title,body,headRefName,createdAt", "--limit", "200"],
        timeout=30,
    )
    if code != 0:
        return None, err.strip() or f"exit {code}"
    try:
        return json.loads(out), None
    except json.JSONDecodeError as e:
        return None, f"could not parse gh pr list JSON: {e}"


def get_pr_changed_files(repo, pr_number):
    code, out, err = run_cmd(
        ["gh", "pr", "diff", str(pr_number), "--repo", f"{GH_ORG}/{repo}", "--name-only"],
        timeout=30,
    )
    if code != 0:
        return None, err.strip() or f"exit {code}"
    return {line.strip() for line in out.splitlines() if line.strip()}, None


def _pr_citation_text(pr):
    return " ".join(str(pr.get(f) or "") for f in ("title", "body", "headRefName"))


def _pr_is_recent(pr, cutoff_dt):
    """Pure function. PRs with a missing/unparseable createdAt are treated
    as NOT recent -- a conservative default that keeps the secondary
    file-overlap check narrow rather than accidentally widening it."""
    raw = pr.get("createdAt")
    if not raw:
        return False
    try:
        created = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    return created >= cutoff_dt


def detect_pr_citation_collisions(repo, prs):
    """PRIMARY signal (UMR-20260806-043900-8c48): pairwise same-citation-
    token overlap across ALL open PRs in `repo` passed in `prs` (a real
    `gh pr list --json number,title,body,headRefName,createdAt` result
    list) -- no time scoping, a same-UMR/task-identity citation is real
    regardless of PR age."""
    pr_tokens = {}
    for pr in prs:
        tokens = extract_citation_tokens(_pr_citation_text(pr))
        if tokens:
            pr_tokens[pr["number"]] = {"title": pr.get("title"), "tokens": tokens}
    nums = sorted(pr_tokens.keys())
    collisions = []
    for i in range(len(nums)):
        for j in range(i + 1, len(nums)):
            a, b = nums[i], nums[j]
            shared = pr_tokens[a]["tokens"] & pr_tokens[b]["tokens"]
            if shared:
                collisions.append({
                    "kind": "pr_citation", "repo": repo, "pr_a": a, "pr_b": b,
                    "pr_a_title": pr_tokens[a]["title"], "pr_b_title": pr_tokens[b]["title"],
                    "shared_citations": sorted(shared),
                })
    return {"pr_with_citation_count": len(pr_tokens), "collisions": collisions}


def detect_pr_file_collisions(repo, prs, max_age_hours=COLLISION_FILE_OVERLAP_MAX_AGE_HOURS,
                               max_workers=COLLISION_GH_MAX_WORKERS, deadline=None):
    """SECONDARY signal (UMR-20260806-043900-8c48): file-overlap, narrowed
    to (a) PRs opened within the last `max_age_hours` hours only -- not the
    full historical backlog -- and (b) shared files outside
    COLLISION_FILE_OVERLAP_EXCLUDE_FILES only (a shared lockfile/scratch-doc/
    shared-schema touch is normal weekly churn, never real signal alone).
    `prs` is the same real `gh pr list` result list detect_pr_citation_collisions()
    takes -- fetched once by the caller, not re-fetched here.

    Real perf fix (SCRIPT_VERSION 3.2.0): the real `gh pr diff --name-only`
    call for each recent PR is still exactly one real subprocess call per PR
    (same O(n) shape PR#120 already established, not re-derived here) but
    now fetched CONCURRENTLY (bounded by `max_workers`) instead of one at a
    time -- at real live PR counts (94 combined recent-PR calls across both
    tracked repos, confirmed at investigation time) the old sequential loop
    could sum past an hour even with each individual call's already-real 30s
    timeout. `deadline`, if given, is a real time.monotonic() cutoff for the
    whole collision section (see get_collision_detection_section()); any PR
    whose fetch has not started by the time the deadline passes is recorded
    in `skipped_due_to_time_budget` -- an honest "checked N of M" outcome,
    never a silent drop and never a block past the budget."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    recent_prs = [pr for pr in prs if _pr_is_recent(pr, cutoff)]
    pr_files = {}
    errors = []
    skipped_due_to_time_budget = []

    def fetch(pr):
        n = pr["number"]
        if deadline is not None and time.monotonic() >= deadline:
            return n, pr.get("title"), None, None, True
        files, ferr = get_pr_changed_files(repo, n)
        return n, pr.get("title"), files, ferr, False

    if recent_prs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, max_workers)) as ex:
            for n, title, files, ferr, skipped in ex.map(fetch, recent_prs):
                if skipped:
                    skipped_due_to_time_budget.append(n)
                    continue
                if ferr:
                    errors.append({"repo": repo, "pr": n, "error": ferr})
                    continue
                pr_files[n] = {"title": title, "files": files - COLLISION_FILE_OVERLAP_EXCLUDE_FILES}

    nums = sorted(pr_files.keys())
    collisions = []
    for i in range(len(nums)):
        for j in range(i + 1, len(nums)):
            a, b = nums[i], nums[j]
            shared = pr_files[a]["files"] & pr_files[b]["files"]
            if shared:
                collisions.append({
                    "kind": "pr_file", "repo": repo, "pr_a": a, "pr_b": b,
                    "pr_a_title": pr_files[a]["title"], "pr_b_title": pr_files[b]["title"],
                    "shared_files": sorted(shared),
                })
    return {
        "recent_pr_count": len(recent_prs),
        "collisions": collisions,
        "errors": errors,
        "skipped_due_to_time_budget": sorted(skipped_due_to_time_budget),
    }


def parse_running_collision_unit_names(stdout):
    """Pure function: extracts real veridian-worker@*/veridian-supervisor@*
    unit names from `systemctl --user list-units <glob> --state=running`
    table output. Deterministic regex on the exact unit-name shape, same
    "don't fuzzy-match" spirit as parse_worker_count()."""
    return re.findall(r"^\s*((?:veridian-worker|veridian-supervisor)@\S+\.service)\s", stdout, re.MULTILINE)


def get_running_collision_units():
    names = []
    for glob in WORKER_COLLISION_UNIT_GLOBS:
        code, out, err = run_cmd(["systemctl", "--user", "list-units", glob, "--state=running"])
        if code == 0:
            names.extend(parse_running_collision_unit_names(out))
    return sorted(set(names))


def get_unit_working_directory(unit_name):
    code, out, err = run_cmd(["systemctl", "--user", "show", unit_name, "-p", "WorkingDirectory"])
    if code != 0:
        return None
    m = re.search(r"^WorkingDirectory=(.*)$", out, re.MULTILINE)
    wd = m.group(1).strip() if m else ""
    return wd or None


def get_unit_prompt_umrs(unit_name):
    """Despite the name (kept for backward compat), returns the PRIMARY
    combined citation-token set (UMR ids + task-identity tokens) found in
    the unit's real prompt.txt -- see extract_citation_tokens()."""
    wd = get_unit_working_directory(unit_name)
    if not wd:
        return None, f"could not determine real WorkingDirectory for {unit_name}"
    prompt_path = os.path.join(wd, "prompt.txt")
    try:
        with open(prompt_path) as f:
            text = f.read()
    except OSError as e:
        return None, f"could not read {prompt_path}: {e}"
    return extract_citation_tokens(text), None


def detect_worker_umr_collisions():
    units = get_running_collision_units()
    unit_tokens = {}
    errors = []
    for u in units:
        tokens, err = get_unit_prompt_umrs(u)
        if err:
            errors.append({"unit": u, "error": err})
            continue
        if tokens:
            unit_tokens[u] = tokens
    unit_list = sorted(unit_tokens.keys())
    collisions = []
    for i in range(len(unit_list)):
        for j in range(i + 1, len(unit_list)):
            a, b = unit_list[i], unit_list[j]
            shared = unit_tokens[a] & unit_tokens[b]
            if shared:
                collisions.append({
                    "kind": "worker_citation", "unit_a": a, "unit_b": b,
                    "shared_citations": sorted(shared),
                })
    return {"checked_units": unit_list, "collisions": collisions, "errors": errors}


def _rank_collision_candidates(candidates):
    """Pure function: PRIMARY-signal candidates ('pr_citation'/
    'worker_citation') ranked before SECONDARY ('pr_file'). Stable within
    each group (preserves original discovery order) -- a fixed, documented
    ordering rule, not a scored/AI judgment."""
    primary_kinds = {"pr_citation", "worker_citation"}
    primary = [c for c in candidates if c["kind"] in primary_kinds]
    secondary = [c for c in candidates if c["kind"] not in primary_kinds]
    return primary + secondary


def _cap_collision_candidates(ranked, trigger=COLLISION_CANDIDATE_CAP_TRIGGER, top_k=COLLISION_TOP_K):
    """Pure function: the hard safety-net cap. Returns (capped_bool,
    shown_list). Applied unconditionally, regardless of whether the
    primary/secondary redefinition above already narrowed the real count --
    a defensive backstop, not a substitute for the real narrowing."""
    total = len(ranked)
    capped = total > trigger
    shown = ranked[:top_k] if capped else ranked
    return capped, shown


def get_collision_detection_section(time_budget_seconds=COLLISION_SECTION_TIME_BUDGET_SECONDS):
    """Real perf fix (SCRIPT_VERSION 3.2.0): `time_budget_seconds` sets a
    real time.monotonic() deadline for the whole section's `gh` work,
    computed once here and passed down to every detect_pr_file_collisions()
    call so the budget is shared across BOTH tracked repos, not reset per
    repo (a per-repo-only budget could still double the real worst case).
    The PRIMARY citation signal (detect_pr_citation_collisions) is pure
    in-memory work over the already-fetched `gh pr list` JSON -- zero extra
    real subprocess calls -- so it is never deadline-limited."""
    deadline = time.monotonic() + time_budget_seconds if time_budget_seconds else None
    by_repo_citation = {}
    by_repo_file = {}
    errors = []
    for repo in COLLISION_TRACKED_REPOS:
        prs, err = get_open_pr_list(repo)
        if err:
            errors.append({"repo": repo, "error": err})
            continue
        by_repo_citation[repo] = detect_pr_citation_collisions(repo, prs)
        file_result = detect_pr_file_collisions(repo, prs, deadline=deadline)
        errors.extend(file_result.pop("errors"))
        by_repo_file[repo] = file_result

    worker_result = detect_worker_umr_collisions()

    pr_citation_pairs = [c for r in by_repo_citation.values() for c in r["collisions"]]
    pr_file_pairs = [c for r in by_repo_file.values() for c in r["collisions"]]
    worker_pairs = worker_result["collisions"]

    all_candidates = pr_citation_pairs + worker_pairs + pr_file_pairs
    ranked = _rank_collision_candidates(all_candidates)
    total_candidates = len(ranked)
    capped, shown = _cap_collision_candidates(ranked)
    total_skipped_due_to_time_budget = sum(
        len(r.get("skipped_due_to_time_budget", [])) for r in by_repo_file.values())

    return {
        "collision_detected": total_candidates > 0,
        "tracked_repos": list(COLLISION_TRACKED_REPOS),
        "checked_unit_globs": list(WORKER_COLLISION_UNIT_GLOBS),
        "file_overlap_max_age_hours": COLLISION_FILE_OVERLAP_MAX_AGE_HOURS,
        "file_overlap_excluded_files": sorted(COLLISION_FILE_OVERLAP_EXCLUDE_FILES),
        "gh_max_workers": COLLISION_GH_MAX_WORKERS,
        "time_budget_seconds": time_budget_seconds,
        "total_skipped_due_to_time_budget": total_skipped_due_to_time_budget,
        "total_candidate_count": total_candidates,
        "primary_candidate_count": len(pr_citation_pairs) + len(worker_pairs),
        "secondary_candidate_count": len(pr_file_pairs),
        "capped": capped,
        "shown_count": len(shown),
        "shown_collisions": shown,
        "by_repo_citation": by_repo_citation,
        "by_repo_file": by_repo_file,
        "worker_umr_collisions": worker_result,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Section 13 (UMR-20260806-041307-0bfd): deterministic instruction-quality
# check over the real last 20 umr_tasks rows. See module docstring for the
# exact fixed rule (a)/(b)/(c) definitions.
# ---------------------------------------------------------------------------
def get_last_n_umr_tasks(sbr, n=20):
    """Real fix (UMR-20260806-041307-0bfd, post-merge correction -- see
    module docstring Section 13 and PROGRESS.md): scoped to
    task_kind = 'veridian_task_create', NOT the raw unfiltered last N
    umr_tasks rows. Confirmed live against this real database before this
    fix was written: the raw unfiltered query returns 100% task_kind=
    'systemctl_action' bookkeeping rows (no 'prompt' field at all) during
    normal dispatch-tick operation, which silently defeated
    DETERMINISTIC_INSTRUCTION_COUNT (permanently 0/20) rather than
    reflecting real dispatch-instruction quality."""
    try:
        conn = sbr._connect()
        rows = conn.execute(
            "SELECT umr_id, ts_submitted, inputs_json FROM umr_tasks "
            "WHERE task_kind = 'veridian_task_create' "
            "ORDER BY ts_submitted DESC LIMIT ?", (n,)
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return None, str(e)
    return [dict(r) for r in rows], None


def extract_prompt_text(inputs_json_text):
    """Pure function: pulls inputs_json.prompt back out as a string, or None
    if it is missing/not a string/unparseable. Never raises."""
    if not inputs_json_text:
        return None
    try:
        parsed = json.loads(inputs_json_text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    prompt = parsed.get("prompt")
    return prompt if isinstance(prompt, str) else None


def check_instruction_quality(prompt_text):
    """Fixed, deterministic 3-rule check -- see module docstring Section 13
    for the exact definition of each rule. Zero AI/LLM judgment: every rule
    is a regex/substring check over real text."""
    if not prompt_text:
        return {
            "passed": False,
            "rule_a_umr_citation_present": False,
            "rule_b_vague_verbs_absent": False,
            "rule_c_concrete_completion_present": False,
            "reasons": ["no string 'prompt' field found in this row's inputs_json"],
        }

    reasons = []
    rule_a = bool(UMR_CITATION_RE.search(prompt_text))
    if not rule_a:
        reasons.append("no UMR-YYYYMMDD-HHMMSS-xxxx citation found in prompt text")

    lowered = prompt_text.lower()
    found_vague = [v for v in VAGUE_VERB_PHRASES if v in lowered]
    rule_b = not found_vague
    if not rule_b:
        reasons.append(f"vague open-ended verb(s) present: {', '.join(found_vague)}")

    rule_c = bool(
        CONCRETE_COMPLETION_FILE_PATH_RE.search(prompt_text)
        or CONCRETE_COMPLETION_PR_REF_RE.search(prompt_text)
    )
    if not rule_c:
        reasons.append(
            "no concrete completion signal found (no file-path-looking token with a "
            "known extension, and no 'PR #<N>'/'pull request #<N>' reference)"
        )

    return {
        "passed": rule_a and rule_b and rule_c,
        "rule_a_umr_citation_present": rule_a,
        "rule_b_vague_verbs_absent": rule_b,
        "rule_c_concrete_completion_present": rule_c,
        "reasons": reasons,
    }


def get_instruction_quality_section(sbr):
    rows, err = get_last_n_umr_tasks(sbr, 20)
    if err:
        return {"error": err, "total_checked": 0, "pass_count": 0, "results": [], "failing": []}

    results = []
    pass_count = 0
    for r in rows:
        prompt_text = extract_prompt_text(r.get("inputs_json"))
        check = check_instruction_quality(prompt_text)
        if check["passed"]:
            pass_count += 1
        results.append({"umr_id": r["umr_id"], "ts_submitted": r["ts_submitted"], **check})

    return {
        "error": None,
        "total_checked": len(rows),
        "pass_count": pass_count,
        "results": results,
        "failing": [r for r in results if not r["passed"]],
    }


# ---------------------------------------------------------------------------
# Section 14 (UMR-20260806-070018-d88b item 4, extended by
# UMR-20260806-071942-5132): real UMR closure tracking, scoped to
# source_trigger='owner_dispatch_gateway' -- so the PM can see, at a glance,
# whether real Owner-dispatched directives are actually closing out or
# piling up. Two real, independent SQL reads, zero AI/LLM judgment:
#   (a) ALL-TIME status breakdown + the real oldest still-open row's age
#       (status IN OWNER_UMR_OPEN_STATUSES -- the umr_tasks CHECK
#       constraint's own non-terminal values: queued/dispatched/running).
#   (b) TRAILING-24H status breakdown + PERCENT_COMPLETE_24H_OWNER_UMR_SET
#       (UMR-20260806-071942-5132): pure arithmetic, (count of status in
#       OWNER_UMR_CLOSED_STATUSES / real total rows in the 24h window) * 100,
#       rounded to 1 decimal. None (not 0.0/100.0) when the window has zero
#       real rows -- an honest no-data report, same spirit as Section 10's
#       "insufficient_data".
# ---------------------------------------------------------------------------
def get_owner_dispatch_umr_status_counts(sbr, since_iso=None):
    """Pure real-SQL read: status -> real COUNT(*) for
    source_trigger='owner_dispatch_gateway' rows, optionally restricted to
    ts_submitted >= since_iso. Never raises; returns (None, err) on a real
    sqlite3.Error so callers report it honestly rather than crash the whole
    report."""
    try:
        conn = sbr._connect()
        if since_iso is not None:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM umr_tasks "
                "WHERE source_trigger = ? AND ts_submitted >= ? GROUP BY status",
                (OWNER_DISPATCH_SOURCE_TRIGGER, since_iso),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM umr_tasks WHERE source_trigger = ? GROUP BY status",
                (OWNER_DISPATCH_SOURCE_TRIGGER,),
            ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return None, str(e)
    return {r["status"]: r["n"] for r in rows}, None


def get_oldest_open_owner_umr(sbr):
    """Pure real-SQL read: the single oldest (MIN ts_submitted) real
    owner_dispatch_gateway row whose status is still one of
    OWNER_UMR_OPEN_STATUSES. Returns (None, None) -- not a fabricated row --
    when no real open row exists."""
    placeholders = ",".join("?" for _ in OWNER_UMR_OPEN_STATUSES)
    try:
        conn = sbr._connect()
        row = conn.execute(
            f"SELECT umr_id, ts_submitted, status FROM umr_tasks "
            f"WHERE source_trigger = ? AND status IN ({placeholders}) "
            f"ORDER BY ts_submitted ASC LIMIT 1",
            (OWNER_DISPATCH_SOURCE_TRIGGER, *OWNER_UMR_OPEN_STATUSES),
        ).fetchone()
        conn.close()
    except sqlite3.Error as e:
        return None, str(e)
    return (dict(row) if row else None), None


def _age_hours_since(iso_ts, now_dt=None):
    """Pure function: real hours between `iso_ts` and now (or `now_dt` if
    given, for deterministic tests), rounded to 1 decimal, or None if
    `iso_ts` is missing/unparseable -- never a fabricated age."""
    if not iso_ts:
        return None
    try:
        then = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    now_dt = now_dt or datetime.now(timezone.utc)
    return round((now_dt - then).total_seconds() / 3600.0, 1)


def get_owner_umr_closure_section(sbr, now_dt=None):
    now_dt = now_dt or datetime.now(timezone.utc)
    empty_24h = {
        "trailing_24h_status_counts": {}, "trailing_24h_total": 0,
        "trailing_24h_closed_count": 0, "percent_complete_24h_owner_umr_set": None,
    }

    all_time_counts, err_all = get_owner_dispatch_umr_status_counts(sbr)
    if err_all:
        return {
            "error": err_all, "all_time_status_counts": {}, "all_time_total": 0,
            "oldest_open_umr_id": None, "oldest_open_status": None, "oldest_open_age_hours": None,
            **empty_24h,
        }

    oldest_open, err_oldest = get_oldest_open_owner_umr(sbr)
    if err_oldest:
        return {
            "error": err_oldest, "all_time_status_counts": all_time_counts,
            "all_time_total": sum(all_time_counts.values()),
            "oldest_open_umr_id": None, "oldest_open_status": None, "oldest_open_age_hours": None,
            **empty_24h,
        }

    since_24h = (now_dt - timedelta(hours=24)).isoformat()
    trailing_counts, err_24h = get_owner_dispatch_umr_status_counts(sbr, since_iso=since_24h)
    if err_24h:
        trailing_counts = None

    if trailing_counts is None:
        section_24h = {**empty_24h}
    else:
        trailing_total = sum(trailing_counts.values())
        closed_24h = sum(n for status, n in trailing_counts.items() if status in OWNER_UMR_CLOSED_STATUSES)
        section_24h = {
            "trailing_24h_status_counts": trailing_counts,
            "trailing_24h_total": trailing_total,
            "trailing_24h_closed_count": closed_24h,
            "percent_complete_24h_owner_umr_set": (
                round(closed_24h / trailing_total * 100, 1) if trailing_total > 0 else None
            ),
        }

    return {
        "error": err_24h,
        "all_time_status_counts": all_time_counts,
        "all_time_total": sum(all_time_counts.values()),
        "oldest_open_umr_id": oldest_open["umr_id"] if oldest_open else None,
        "oldest_open_status": oldest_open["status"] if oldest_open else None,
        "oldest_open_age_hours": _age_hours_since(oldest_open["ts_submitted"], now_dt) if oldest_open else None,
        **section_24h,
    }


# ---------------------------------------------------------------------------
# Assembly + rendering
# ---------------------------------------------------------------------------
def build_report(sbr):
    ram_swap = get_ram_swap()
    load_avg = get_load_average()
    dispatch = get_dispatch_tick_status()
    workers = get_worker_count()
    stuck = get_stuck_tasks()
    tmux = get_tmux_status()
    estop = get_emergency_stop()
    db_integrity = get_db_integrity(sbr)

    gtm_section = get_gtm_section(sbr)
    ocid_section = get_ocid_registry_section(sbr)
    umr_section = get_umr_tasks_section(sbr)

    readiness = compute_readiness_bucket(gtm_section)

    prior, prior_err = get_prior_snapshot(sbr)

    current_flat = {
        "gtm_pass_count": gtm_section.get("gtm_pass_count"),
        "gtm_fail_count": gtm_section.get("gtm_fail_count"),
        # NOTE: pm_report_snapshots' schema (already-migrated, real) has
        # separate gtm_blocked_count / gtm_pending_count columns, but the
        # only real established source data (gtm_certification_categories)
        # does not distinguish blocked from pending (see module docstring).
        # Both columns are written with the same combined
        # blocked_or_pending count so neither column is silently left NULL,
        # and this collapse is documented here rather than hidden.
        "gtm_blocked_count": gtm_section.get("gtm_blocked_or_pending_count"),
        "gtm_pending_count": gtm_section.get("gtm_blocked_or_pending_count"),
        "mem_available_mb": ram_swap.get("mem_available_mb"),
        "swap_free_pct": ram_swap.get("swap_free_pct"),
        "load_1min": load_avg.get("load_1min"),
        "load_5min": load_avg.get("load_5min"),
        "load_15min": load_avg.get("load_15min"),
        "dispatch_tick_active": dispatch.get("dispatch_tick_active"),
        "parallel_worker_count": workers.get("parallel_worker_count"),
        "stuck_task_count": stuck.get("stuck_task_count"),
        "tmux_session_alive": tmux.get("tmux_session_alive"),
        "emergency_stop_present": estop.get("emergency_stop_present"),
        "db_integrity_ok": db_integrity.get("db_integrity_ok"),
        "umr_tasks_total": umr_section.get("umr_tasks_total"),
        "ocid_canonical_registry_total": ocid_section.get("ocid_canonical_registry_total"),
    }
    deltas = compute_deltas(prior, current_flat)

    open_issues = build_open_issues(gtm_section, db_integrity, ram_swap, load_avg)
    decisions = get_pm_decisions_pending(sbr)
    owner_proposals = get_owner_proposals_pending(sbr)

    # Sections 9-13 (UMR-20260806-041307-0bfd) -- see module docstring.
    ocid_compliance_audit_section = get_ocid_compliance_audit_section(prior)
    trend_analysis_section = get_trend_analysis(sbr)
    stall_detection_section = get_stuck_tasks_detail()
    collision_detection_section = get_collision_detection_section()
    instruction_quality_section = get_instruction_quality_section(sbr)
    # Section 14 (UMR-20260806-070018-d88b item 4, extended by
    # UMR-20260806-071942-5132) -- see module docstring.
    owner_umr_closure_section = get_owner_umr_closure_section(sbr)

    report = {
        "report_format_version": REPORT_FORMAT_VERSION,
        "script_version": SCRIPT_VERSION,
        "generated_at": _now_iso(),
        "umr": "UMR-20260805-181636-32f2",
        "parent_umr": "UMR-20260802-165606-4413",
        "ocid": "OCID-020",
        "header_status": {
            "ram_swap": ram_swap,
            "load_average": load_avg,
            "dispatch_tick": dispatch,
            "parallel_workers": workers,
            "stuck_tasks": stuck,
            "tmux": tmux,
            "emergency_stop": estop,
            "db_integrity": db_integrity,
        },
        "ocid_020_gtm_section": gtm_section,
        "ocid_canonical_registry_section": ocid_section,
        "umr_tasks_section": umr_section,
        "gtm_readiness": readiness,
        "implementation_summary": {
            "prior_snapshot_found": prior is not None,
            "prior_snapshot_error": prior_err,
            "prior_snapshot_ts": prior.get("ts") if prior else None,
            "deltas": deltas,
        },
        "open_issues": open_issues,
        "pm_decisions_pending": decisions,
        "owner_proposals_pending": owner_proposals,
        "ocid_compliance_audit_section": ocid_compliance_audit_section,
        "trend_analysis_section": trend_analysis_section,
        "stall_detection_section": stall_detection_section,
        "collision_detection_section": collision_detection_section,
        "instruction_quality_section": instruction_quality_section,
        "owner_umr_closure_section": owner_umr_closure_section,
        "current_flat_fields": current_flat,
        "thresholds": {
            "SWAP_FREE_PCT_WARN_THRESHOLD": SWAP_FREE_PCT_WARN_THRESHOLD,
            "LOAD_1MIN_WARN_THRESHOLD": LOAD_1MIN_WARN_THRESHOLD,
            "TREND_STABLE_TOLERANCE_PCT": TREND_STABLE_TOLERANCE_PCT,
        },
    }
    return report


def render_report_text(report):
    lines = []

    def h(title):
        lines.append("")
        lines.append("=" * 78)
        lines.append(title)
        lines.append("=" * 78)

    lines.append(f"PM REPORT v3 (real, pure, deterministic -- zero AI/LLM calls)")
    lines.append(f"script_version: {report.get('script_version', SCRIPT_VERSION)}")
    lines.append(f"generated_at: {report['generated_at']}")
    lines.append(f"umr: {report['umr']}  parent_umr: {report['parent_umr']}  ocid: {report['ocid']}")

    h("1. HEADER / STATUS")
    hs = report["header_status"]
    rs = hs["ram_swap"]
    la = hs["load_average"]
    lines.append(f"RAM available: {rs.get('mem_available_mb')} MB / total {rs.get('mem_total_mb')} MB")
    lines.append(f"Swap free: {rs.get('swap_free_mb')} MB / total {rs.get('swap_total_mb')} MB "
                  f"({rs.get('swap_free_pct')}%)")
    lines.append(f"Load average: 1m={la.get('load_1min')} 5m={la.get('load_5min')} 15m={la.get('load_15min')}")
    lines.append(f"dispatch-tick timer active: {hs['dispatch_tick'].get('dispatch_tick_active')}")
    lines.append(f"Parallel workers running: {hs['parallel_workers'].get('parallel_worker_count')}")
    lines.append(f"Stuck tasks (STUCK_TASKS_HEARTBEAT.json): {hs['stuck_tasks'].get('stuck_task_count')} "
                  f"(heartbeat generated_at={hs['stuck_tasks'].get('heartbeat_generated_at')})")
    lines.append(f"tmux session 'claude' alive: {hs['tmux'].get('tmux_session_alive')}")
    lines.append(f"EMERGENCY_STOP present: {hs['emergency_stop'].get('emergency_stop_present')} "
                  f"(path={hs['emergency_stop'].get('emergency_stop_path')})")
    lines.append(f"DB integrity_check OK: {hs['db_integrity'].get('db_integrity_ok')} "
                  f"(rows={hs['db_integrity'].get('integrity_check_rows')})")

    h("2. OCID-020 GTM CERTIFICATION SECTION")
    gtm = report["ocid_020_gtm_section"]
    lines.append(f"pass={gtm.get('gtm_pass_count')} fail={gtm.get('gtm_fail_count')} "
                  f"blocked_or_pending={gtm.get('gtm_blocked_or_pending_count')} "
                  f"(blocked/pending is a single combined state in the real source data "
                  f"-- see module docstring)")
    ocid = report["ocid_canonical_registry_section"]
    lines.append(f"ocid_canonical_registry: total={ocid.get('ocid_canonical_registry_total')} "
                  f"fully_complete={ocid.get('ocid_canonical_registry_fully_complete')}")
    lines.append(f"ocid_canonical_registry by status: {ocid.get('ocid_canonical_registry_by_status')}")

    h("3. TEST RESULTS + DETERMINISTIC GATE")
    for cat in gtm.get("categories", []):
        lines.append(f"  [{cat['category_index']:2d}] {cat['category_name']:<28s} "
                      f"state={cat['state']:<18s} validated_at={cat['validated_at']}")
    gate = gtm.get("deterministic_gate", {})
    lines.append("")
    lines.append(f"Deterministic gate result: {gate.get('gate_result')}")
    lines.append(f"Deterministic gate definition: {gate.get('definition')}")

    h("4. GO-TO-MARKET READINESS SCORE + RECOMMENDATION")
    readiness = report["gtm_readiness"]
    marker = " \u26a0 PLACEHOLDER" if readiness.get("is_placeholder") else ""
    lines.append(f"Recommendation: {readiness.get('bucket')}{marker}")
    lines.append(f"Reason: {readiness.get('reason')}")

    h("5. IMPLEMENTATION SUMMARY (deltas since prior report)")
    impl = report["implementation_summary"]
    if not impl["prior_snapshot_found"]:
        extra = f" (error: {impl['prior_snapshot_error']})" if impl.get("prior_snapshot_error") else ""
        lines.append(f"No prior pm_report_snapshots row found -- this is the first real run.{extra}")
    else:
        lines.append(f"Prior snapshot ts: {impl['prior_snapshot_ts']}")
    for field, delta in impl["deltas"].items():
        lines.append(f"  {field:<28s} {delta}")

    h("6. OPEN ISSUES (auto-generated, pure threshold/rule logic)")
    issues = report["open_issues"]
    if not issues:
        lines.append("None.")
    for i, issue in enumerate(issues, 1):
        if issue["kind"] == "gtm_category_failed":
            lines.append(f"  {i}. [GTM FAIL] category {issue['category_index']} "
                          f"'{issue['category_name']}' ({issue['ocid_number']}): {issue['root_cause']}")
        elif issue["kind"] == "db_integrity_check_failed":
            lines.append(f"  {i}. [DB INTEGRITY] {issue['detail']} rows={issue['integrity_check_rows']}")
        elif issue["kind"] == "swap_free_low":
            lines.append(f"  {i}. [SWAP LOW] {issue['detail']}")
        elif issue["kind"] == "load_average_high":
            lines.append(f"  {i}. [LOAD HIGH] {issue['detail']}")
        else:
            lines.append(f"  {i}. [{issue['kind']}] {issue}")

    h("7. PM DECISION REQUIRED (read-only from pm_decisions_pending)")
    decisions = report["pm_decisions_pending"]
    if isinstance(decisions, dict) and "error" in decisions:
        lines.append(f"ERROR reading pm_decisions_pending: {decisions['error']}")
    elif not decisions:
        lines.append("None open.")
    else:
        for d in decisions:
            lines.append(f"  #{d['id']} [{d['opened_ts']}] {d['title']}")
            lines.append(f"      detail: {d['detail']}")
            lines.append(f"      recommended_option: {d['recommended_option']}")
            lines.append(f"      related_umr: {d['related_umr']}")

    h("8. AI PROPOSALS AWAITING PM DECISION (read-only from pm_decisions_pending, "
      "decision_type='owner_proposal')")
    proposals = report["owner_proposals_pending"]
    if isinstance(proposals, dict) and "error" in proposals:
        lines.append(f"ERROR reading owner proposals: {proposals['error']}")
    elif not proposals:
        lines.append("None open.")
    else:
        for pr in proposals:
            lines.append(f"  #{pr['id']} [{pr['opened_ts']}] child_umr={pr['child_umr']}")
            lines.append(f"      issue: {pr['issue']}")
            lines.append(f"      proposed: {pr['proposal']}")

    h("9. DATABASE VALIDATION FOLD-IN (audit_ocid_compliance.py --report, "
      "shelled out to real, not re-derived)")
    audit = report["ocid_compliance_audit_section"]
    if audit.get("error"):
        lines.append(f"ERROR running/parsing audit_ocid_compliance.py --report: {audit['error']}")
    else:
        lines.append(f"audit rows: {audit['total_rows']}  "
                      f"audit_passed=true: {audit['audit_passed_count']}  "
                      f"audit_passed=false: {audit['audit_failed_count']}")
        lines.append(f"prior_baseline_available: {audit['prior_baseline_available']}")
        if audit["prior_baseline_available"]:
            if audit["newly_failing_ocids"]:
                lines.append(f"NEWLY FAILING OCIDs (real, vs prior report): {audit['newly_failing_ocids']}")
            else:
                lines.append("NEWLY FAILING OCIDs (real, vs prior report): none")
        else:
            lines.append("NEWLY FAILING OCIDs: n/a (no prior report_json baseline to diff against yet)")
        if audit["failing_ocids"]:
            lines.append(f"All currently audit_passed=false OCIDs: {audit['failing_ocids']}")

    h("10. 10-REPORT TREND ANALYSIS (pure arithmetic, first-half vs second-half average)")
    trend = report["trend_analysis_section"]
    if trend.get("error"):
        lines.append(f"ERROR computing trend analysis: {trend['error']}")
    elif trend["rows_used"] == 0:
        lines.append(f"No pm_report_snapshots rows available -- {trend.get('note', 'no trend computed')}.")
    else:
        lines.append(f"rows_used: {trend['rows_used']} (window requested: {trend['window_size_requested']}, "
                      f"stable tolerance: +/-{trend['stable_tolerance_pct']}%)")
        for metric, m in trend["metrics"].items():
            if m.get("trend") == "insufficient_data":
                lines.append(f"  {metric:<18s} insufficient_data (rows_used={m.get('rows_used')})")
            else:
                lines.append(f"  {metric:<18s} trend={m['trend']:<10s} "
                              f"first_half_avg={m['first_half_avg']} second_half_avg={m['second_half_avg']} "
                              f"pct_change={m['pct_change']}")

    h("11. DETERMINISTIC STALL DETECTION (STUCK_TASKS_HEARTBEAT.json, real per-task lines)")
    stall = report["stall_detection_section"]
    if stall.get("error"):
        lines.append(f"ERROR reading STUCK_TASKS_HEARTBEAT.json: {stall['error']}")
    else:
        lines.append(f"stuck_task_threshold_minutes: {stall['stuck_task_threshold_minutes']}  "
                      f"stuck_task_count: {len(stall['tasks'])}")
        for t in stall["tasks"]:
            lines.append(f"  task_id={t['task_id']} blocked_minutes={t['blocked_minutes']} "
                          f"last_note={t['last_note']}")

    h("12. DETERMINISTIC COLLISION DETECTION (primary: same UMR/task-identity citation; "
      "secondary: recent file-overlap, common-file exclude list applied; hard-capped output)")
    collision = report["collision_detection_section"]
    lines.append(f"COLLISION_DETECTED={'YES' if collision['collision_detected'] else 'NO'}")
    lines.append(f"tracked_repos: {collision['tracked_repos']}  "
                  f"checked_unit_globs: {collision['checked_unit_globs']}")
    lines.append(f"candidates: total={collision['total_candidate_count']}  "
                  f"primary(same UMR/task-identity)={collision['primary_candidate_count']}  "
                  f"secondary(file-overlap, opened <= {collision['file_overlap_max_age_hours']}h ago, "
                  f"excludes {collision['file_overlap_excluded_files']})={collision['secondary_candidate_count']}")
    if collision["capped"]:
        lines.append(f"{collision['total_candidate_count']} candidate collisions found -- showing top "
                      f"{collision['shown_count']} by relevance (primary ranked above secondary).")
    if not collision["shown_collisions"]:
        lines.append("  No real pairwise overlap found.")
    else:
        for c in collision["shown_collisions"]:
            if c["kind"] == "pr_citation":
                lines.append(f"  [PRIMARY: PR CITATION MATCH] {c['repo']}: "
                              f"PR #{c['pr_a']} ({c['pr_a_title']}) <-> PR #{c['pr_b']} ({c['pr_b_title']}) "
                              f"shared citations: {c['shared_citations']}")
            elif c["kind"] == "worker_citation":
                lines.append(f"  [PRIMARY: WORKER/SUPERVISOR CITATION MATCH] {c['unit_a']} <-> {c['unit_b']} "
                              f"shared citations: {c['shared_citations']}")
            elif c["kind"] == "pr_file":
                lines.append(f"  [SECONDARY: RECENT PR FILE OVERLAP] {c['repo']}: "
                              f"PR #{c['pr_a']} ({c['pr_a_title']}) <-> PR #{c['pr_b']} ({c['pr_b_title']}) "
                              f"shared files: {c['shared_files']}")
    for repo, repo_data in collision["by_repo_citation"].items():
        lines.append(f"  ({repo}: {repo_data['pr_with_citation_count']} open PR(s) carry a citation token, "
                      f"checked pairwise)")
    for repo, repo_data in collision["by_repo_file"].items():
        skipped = repo_data.get("skipped_due_to_time_budget") or []
        skip_note = f", {len(skipped)} skipped (time budget exceeded): PR#{skipped}" if skipped else ""
        lines.append(f"  ({repo}: {repo_data['recent_pr_count']} PR(s) opened in the last "
                      f"{collision['file_overlap_max_age_hours']}h, checked pairwise for file overlap, "
                      f"fetched with {collision['gh_max_workers']} concurrent gh calls{skip_note})")
    lines.append(f"  (real gh-call time budget for this section: {collision['time_budget_seconds']}s; "
                  f"{collision['total_skipped_due_to_time_budget']} PR(s) skipped total)")
    if collision["worker_umr_collisions"]["errors"]:
        lines.append(f"  Worker-unit-check errors: {collision['worker_umr_collisions']['errors']}")
    if collision["errors"]:
        lines.append(f"  errors: {collision['errors']}")

    h("13. DETERMINISTIC INSTRUCTION QUALITY CHECK (last 20 real umr_tasks rows, fixed rule-based)")
    iq = report["instruction_quality_section"]
    if iq.get("error"):
        lines.append(f"ERROR reading umr_tasks: {iq['error']}")
    else:
        lines.append(f"DETERMINISTIC_INSTRUCTION_COUNT={iq['pass_count']}/{iq['total_checked']}")
        if iq["failing"]:
            lines.append("Failing rows:")
            for f in iq["failing"]:
                lines.append(f"  umr_id={f['umr_id']} ts_submitted={f['ts_submitted']} "
                              f"reasons={f['reasons']}")
        else:
            lines.append("No failing rows among those checked.")

    h("14. OWNER UMR CLOSURE TRACKING (source_trigger='owner_dispatch_gateway', real umr_tasks rows)")
    ouc = report["owner_umr_closure_section"]
    if ouc.get("error"):
        lines.append(f"ERROR reading umr_tasks: {ouc['error']}")
    else:
        lines.append(f"ALL_TIME_TOTAL={ouc['all_time_total']}  status_breakdown={ouc['all_time_status_counts']}")
        if ouc["oldest_open_umr_id"]:
            lines.append(f"OLDEST_OPEN_UMR={ouc['oldest_open_umr_id']} "
                          f"status={ouc['oldest_open_status']} "
                          f"age_hours={ouc['oldest_open_age_hours']}")
        else:
            lines.append("OLDEST_OPEN_UMR=none (no real row currently in queued/dispatched/running)")
        pct = ouc["percent_complete_24h_owner_umr_set"]
        pct_str = f"{pct}%" if pct is not None else "insufficient_data (0 real rows in trailing 24h)"
        lines.append(f"PERCENT_COMPLETE_24H_OWNER_UMR_SET={pct_str}")
        lines.append(f"  (trailing 24h: total={ouc['trailing_24h_total']} "
                      f"closed={ouc['trailing_24h_closed_count']} "
                      f"status_breakdown={ouc['trailing_24h_status_counts']})")

    lines.append("")
    lines.append("=" * 78)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# DB write + file writes
# ---------------------------------------------------------------------------
def write_snapshot_row(sbr, report):
    fields = report["current_flat_fields"]
    conn = sbr._connect()
    with sbr._write_lock():
        conn.execute(
            """
            INSERT INTO pm_report_snapshots (
                ts, gtm_pass_count, gtm_fail_count, gtm_blocked_count, gtm_pending_count,
                mem_available_mb, swap_free_pct, load_1min, load_5min, load_15min,
                dispatch_tick_active, parallel_worker_count, stuck_task_count,
                tmux_session_alive, emergency_stop_present, db_integrity_ok,
                umr_tasks_total, ocid_canonical_registry_total, report_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report["generated_at"],
                fields["gtm_pass_count"], fields["gtm_fail_count"],
                fields["gtm_blocked_count"], fields["gtm_pending_count"],
                fields["mem_available_mb"], fields["swap_free_pct"],
                fields["load_1min"], fields["load_5min"], fields["load_15min"],
                int(bool(fields["dispatch_tick_active"])) if fields["dispatch_tick_active"] is not None else None,
                fields["parallel_worker_count"], fields["stuck_task_count"],
                int(bool(fields["tmux_session_alive"])) if fields["tmux_session_alive"] is not None else None,
                int(bool(fields["emergency_stop_present"])) if fields["emergency_stop_present"] is not None else None,
                int(bool(fields["db_integrity_ok"])) if fields["db_integrity_ok"] is not None else None,
                fields["umr_tasks_total"], fields["ocid_canonical_registry_total"],
                json.dumps(report),
            ),
        )
        conn.commit()
    conn.close()


def write_report_files(report_text):
    os.makedirs(os.path.dirname(REPORT_LATEST_PATH), exist_ok=True)
    with open(REPORT_LATEST_PATH, "w") as f:
        f.write(report_text)
    os.makedirs(os.path.dirname(REPORT_HISTORY_PATH), exist_ok=True)
    with open(REPORT_HISTORY_PATH, "a") as f:
        f.write(report_text)
        f.write("\n")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-db-write", action="store_true",
                         help="Skip pm_report_snapshots INSERT and report file writes (still prints report).")
    parser.add_argument("--json-out", default=None, help="Also write the full report as JSON to this path.")
    args = parser.parse_args(argv)

    sbr = load_module_from_path("superboss_register", SBR_PATH)

    report = build_report(sbr)
    text = render_report_text(report)
    print(text)

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(report, f, indent=2, default=str)

    if not args.no_db_write:
        write_report_files(text)
        write_snapshot_row(sbr, report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
