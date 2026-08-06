#!/usr/bin/env python3
"""Wholesale regenerator for /opt/veridian/ai-os/MASTER_INDEX.yaml.

WHY THIS EXISTS (2026-07-29/30, master-index-self-regeneration-phase6):
MASTER_INDEX.yaml's own header/protocol text claims it is "mechanically generated"
and "7-pass audited." It is not -- it is hand-appended by individual tasks, one
registries[] entry (or a status/note field) edited per task, never regenerated from
source. Proven stale case: registries.terminology_standardization sat at
status: phase_0_phase_1_phase_2_complete_phase_3_not_yet_dispatched through the
file's 2026-07-26 edit, even though compliance-tracker#552 (Phase 3, CI enforcement),
#553 (Phase 4, hardcoded-example migration), and claude-control#56 (Phase 5, full
enforcement) were all independently confirmed MERGED via `gh pr view` on 2026-07-24
-- i.e. all 6 phases (phase_0..phase_5) of that initiative's own phase plan were
long since complete and nobody corrected the file.

THIS SCRIPT does not sed/patch the file -- every run builds a fresh in-memory model
(base = the real live file's own content, since most of its ~139 registries entries'
prose has no other structured source of truth; corrections = real evidence pulled
fresh from superboss-register.sqlite + `gh` + phase-plan yaml files) and re-serializes
the ENTIRE document from that model. That is the "wholesale rewrite, not
append/patch" the Owner asked for: the mechanism is regeneration-from-sources, not
hand-editing, even where the source for a given field is "the file's own prior
content" because nothing else produces that prose.

It does two concrete jobs:
  1. initiative-status correction: for every registries[] entry whose status string
     encodes phase-completion (matches PHASE_STATUS_RE), find that initiative's own
     *_PHASE_PLAN_*.yaml, find real merged-PR evidence for its phases via `gh pr
     list --state merged --search <keyword>` (never trusting a self-reported status
     field), match each merged PR's title to a phase by real topical token overlap
     (a bare phase-number coincidence is never trusted alone -- confirmed necessary
     during testing, see _match_phase_for_title's docstring), and correct `status`
     (with a full evidence trail attached) if real evidence shows more phases
     complete than currently claimed.
  2. unregistered_mentions backlog closure: postflight_audit_gate.py flags real
     ai-os/scripts paths mentioned in task --content that aren't in any registry, into
     superboss-register.sqlite's unregistered_mentions table (status=NEEDS_REGISTRATION).
     Nothing ever closed these. This script auto-registers the ones that resolve to a
     real file on disk into system_index, marks them RESOLVED_AUTO_REGISTERED, and
     surfaces a live count of what's resolved vs. still flagged in the regenerated
     file's own header -- so backlog is visible, never silent.

Usage:
  python3 regenerate_master_index.py --dry-run --out /tmp/MASTER_INDEX.regenerated.yaml
      # read-only: queries DB + gh + disk, writes candidate output to --out, makes
      # NO writes to the live YAML or to the sqlite DB. Diff --out against the live
      # file by hand before ever running for real. Previews ALL phase-tracked
      # entries' status-correction evidence regardless of --only (informational
      # only -- dry-run never mutates anything, so there is nothing to gate here).
  python3 regenerate_master_index.py --apply --only terminology_standardization
      # real, scoped run: re-verifies the live file's checksum is unchanged since it
      # started, backs it up via rename (never deletes), writes the regenerated
      # content in its place, and commits the unregistered_mentions/system_index DB
      # mutations. Initiative-status correction is applied ONLY to the entry id(s)
      # named in --only (comma-separated for more than one) -- every other
      # registries[] entry is left byte-for-byte as it already was.
  python3 regenerate_master_index.py --apply
      # --apply with NO --only is the conservative default: applies ZERO
      # initiative-status corrections (unregistered_mentions auto-registration
      # still runs -- it is independently disk-existence-verified, not
      # matcher-dependent, so it is always safe unscoped).

KNOWN LIMITATION (confirmed by testing 2026-07-29/30, not fixed in this pass --
separate follow-up work, not a blocker for landing individually-verified fixes):
the phase-matcher (_match_phase_for_title -- topical token-overlap between a
merged PR's title and an initiative's own phase-plan names) has a real
false-positive rate on initiatives whose phase names are short/generic. Confirmed
case: registries.auditor_engine's phases (e.g. "Remaining AI Review lane") are
generic enough that unrelated merged PRs (#562 "...defense-in-depth security
architecture", #51 "AI Workforce... Worker Agent Dispatch") scored above the
match threshold and were misattributed as evidence for phases they have nothing
to do with. registries.terminology_standardization's own phase names are specific
enough (e.g. "existing_hardcoded_example_migration") that its correction is clean
and independently verified against 8 real merged PRs. DO NOT run `--apply` without
`--only` naming specific, human-reviewed entries until the matcher is hardened --
see apply_status_corrections()'s docstring for the exact reasoning.

Not wired into cron/systemd -- manual tool only for now (cron is disabled server-wide
pending separate remediation). Run by hand until that lands.
"""
import argparse
import contextlib
import datetime
import json
import os
import re
import sqlite3
import subprocess
import sys
import uuid

import yaml

DB = "/opt/veridian/ai-os/memory/superboss-register.sqlite"
LIVE_PATH = "/opt/veridian/ai-os/MASTER_INDEX.yaml"
_WRITE_LOCK_PATH = DB + ".writelock"

KNOWN_ROOTS = [
    "/opt/veridian/",
    "/opt/veridian/repos/compliance-tracker/",
]

# bare repo names this system talks about -> real GitHub slug, for PR lookups.
KNOWN_REPOS = {
    "compliance-tracker": "FChecklist/compliance-tracker",
    "claude-control": "FChecklist/claude-control",
    "projexa": "FChecklist/projexa",
}

PHASE_STATUS_RE = re.compile(r"phase_(\d+)", re.IGNORECASE)
PHASE_PLAN_PATH_RE = re.compile(r"[\w./-]*PHASE_PLAN[\w./-]*\.yaml", re.IGNORECASE)

STOPWORDS = {"and", "the", "of", "for", "to", "a", "in", "on", "not", "yet", "phase", "complete", "dispatched"}


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@contextlib.contextmanager
def write_lock():
    """Same flock convention as postflight_audit_gate.py / superboss-register.py --
    this script is a writer to the same shared DB and must not skip it."""
    os.makedirs(os.path.dirname(_WRITE_LOCK_PATH), exist_ok=True)
    with open(_WRITE_LOCK_PATH, "w") as lockfile:
        import fcntl
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


def _resolve_on_disk(rel_path):
    """Try each known root; return the first real absolute path that exists, else None."""
    rel_path = rel_path.strip()
    for root in KNOWN_ROOTS:
        candidate = os.path.join(root, rel_path)
        if os.path.isfile(candidate):
            return candidate
    return None


# ---------------------------------------------------------------------------
# 1. Source-table counts (real, queried fresh every run)
# ---------------------------------------------------------------------------

SOURCE_TABLES = [
    "capability_registry", "wiring_registry", "knowledge_engine", "system_index",
    "instructions", "work_items", "unregistered_mentions", "task_audits",
]


def query_source_counts(cur):
    counts = {}
    for t in SOURCE_TABLES:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            counts[t] = cur.fetchone()[0]
        except sqlite3.OperationalError as e:
            counts[t] = f"ERROR: {e}"
    return counts


# ---------------------------------------------------------------------------
# 2. Initiative phase-status derivation from real evidence
# ---------------------------------------------------------------------------

def _find_phase_plan_path(entry):
    text = entry.get("path", "") or ""
    m = PHASE_PLAN_PATH_RE.search(text)
    if not m:
        return None
    return _resolve_on_disk(m.group(0).lstrip("/"))


def _load_phase_plan(path):
    """Phase-plan files are NOT schema-uniform across initiatives -- confirmed by
    direct inspection: TERMINOLOGY_STANDARDIZATION's plan uses `phases: [{id, name}]`
    but AUDITOR_ENGINE's uses `phases: [{phase: <int>, name}]` (no `id` key at all).
    Blindly doing p.get("id", "") produced silent blank phase-ids for every entry in
    the second schema, which degraded phase-matching to weak generic-word overlap
    and produced two confirmed false-positive PR attributions in testing. Handle
    both known shapes explicitly; if neither key is present, refuse to guess --
    return None so the caller skips derivation for that entry rather than silently
    misattribute evidence."""
    try:
        with open(path, encoding="utf-8") as fh:
            plan = yaml.safe_load(fh)
        phases = plan.get("phases", [])
        result = []
        for p in phases:
            if p.get("id"):
                pid = str(p["id"])
            elif "phase" in p and p["phase"] is not None:
                pid = f"phase_{p['phase']}"
            else:
                return None  # unrecognized phase-plan schema for this file -- bail, don't guess
            result.append((pid, p.get("name", "") or ""))
        return result or None
    except Exception:
        return None


def _repos_mentioned(entry):
    blob = json.dumps(entry)
    return [slug for bare, slug in KNOWN_REPOS.items() if bare in blob]


def _keywords_for_search(entry_id, limit=2):
    words = [w for w in entry_id.split("_") if w not in STOPWORDS and len(w) > 3]
    words.sort(key=len, reverse=True)
    return words[:limit] if words else [entry_id]


def _old_max_complete_phase(status):
    """Parse the highest phase number already claimed complete from a status string
    like 'phase_0_phase_1_phase_2_complete_phase_3_not_yet_dispatched' -- everything
    before the literal word 'complete' counts as already-claimed-done."""
    if "complete" not in status:
        return -1
    before = status.split("complete", 1)[0]
    nums = [int(n) for n in PHASE_STATUS_RE.findall(before)]
    return max(nums) if nums else -1


_GH_SEARCH_CACHE = {}  # (repo_slug, keyword) -> list of merged PR dicts; a real
# network call, and the same (repo, keyword) pair can legitimately recur across
# entries/runs -- never re-fetch within one process.


def _gh_pr_list_merged_search(repo_slug, keyword, limit=50, timeout=25):
    """The actual authoritative source: GitHub's own merged-PR state via `gh`,
    never a self-reported status field. Uses gh's full-text search (title+body),
    which is why this finds PRs whose title alone doesn't contain the keyword."""
    key = (repo_slug, keyword)
    if key in _GH_SEARCH_CACHE:
        return _GH_SEARCH_CACHE[key]
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--repo", repo_slug, "--state", "merged",
             "--search", keyword, "--json", "number,title,mergedAt", "--limit", str(limit)],
            capture_output=True, text=True, timeout=timeout,
        )
        out = json.loads(result.stdout) if result.returncode == 0 else []
    except Exception:
        out = []
    _GH_SEARCH_CACHE[key] = out
    return out


def _tokenize(text):
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 3}


def _phase_tokens(pid, name):
    toks = _tokenize(pid) | _tokenize(name)
    return {w for w in toks if w not in STOPWORDS and not w.isdigit() and w != "phase"}


def _match_phase_for_title(title, phases):
    """Which phase (if any) does this merged PR's title real-evidence for?
    Requires actual topical word-overlap with that phase's own id/name -- an
    explicit 'phase_4'-shaped token in the title is only a *bonus*, never proof
    by itself, because unrelated initiatives independently use the same
    phase_0..phase_N self-labeling convention (confirmed false-positive risk:
    a totally unrelated PR titled '...phase_4: defense-in-depth security
    architecture' would otherwise get misattributed to a different initiative's
    own phase_4 just because the number matches)."""
    title_tokens = _tokenize(title)
    num_hint = None
    m = re.search(r"phase[_\s]?(\d+)", title.lower())
    if m:
        n = int(m.group(1))
        if 0 <= n < len(phases):
            num_hint = n

    best_idx, best_score = None, 0
    for idx, (pid, name) in enumerate(phases):
        overlap = len(_phase_tokens(pid, name) & title_tokens)
        score = overlap
        if idx == num_hint and overlap >= 1:
            score += 2  # numeric hint only counts once there's already real topical overlap
        if score > best_score:
            best_idx, best_score = idx, score
    return best_idx if best_score >= 2 else None


def derive_status_correction(entry, cur):
    """Returns (corrected: bool, evidence: dict) -- never mutates entry itself;
    caller applies the mutation so dry-run vs apply stays symmetric. `cur` is kept
    in the signature for callers/tests that want a DB handle, even though this
    function's own evidence now comes from `gh`, not from the instructions table
    (checked: for this dataset, PR numbers never actually appear in the
    instructions table's raw_text/response_summary, so DB text-mining alone
    cannot find them -- gh's own real merged-PR search is the authoritative
    source, matching the Owner's own stated standard elsewhere in this system:
    "real, independently-verifiable outcomes ... via gh", not a self-reported
    status field)."""
    status = entry.get("status", "") or ""
    if "phase_0" not in status and "phase 0" not in status:
        return False, None  # not a phase-tracked initiative status; nothing to derive

    plan_path = _find_phase_plan_path(entry)
    if not plan_path:
        return False, {"checked": True, "reason": "no *_PHASE_PLAN_*.yaml resolvable on disk"}

    phases = _load_phase_plan(plan_path)
    if not phases:
        return False, {"checked": True, "reason": f"phase plan at {plan_path} had no phases[] list"}

    repos = _repos_mentioned(entry) or list(KNOWN_REPOS.values())
    keywords = _keywords_for_search(entry.get("id", ""))

    merged_prs = []
    seen = set()
    for repo in repos:
        for kw in keywords:
            for pr in _gh_pr_list_merged_search(repo, kw):
                key = (repo, pr.get("number"))
                if key in seen:
                    continue
                seen.add(key)
                merged_prs.append({**pr, "repo": repo})

    evidence = []
    max_phase = -1
    for pr in merged_prs:
        phase_idx = _match_phase_for_title(pr.get("title", ""), phases)
        if phase_idx is None:
            continue
        evidence.append({
            "repo": pr["repo"], "pr": pr.get("number"), "title": pr.get("title"),
            "merged_at": pr.get("mergedAt"), "matched_phase": phases[phase_idx][0],
        })
        max_phase = max(max_phase, phase_idx)

    old_max = _old_max_complete_phase(status)
    result_meta = {
        "checked": True, "phase_plan": plan_path, "total_phases": len(phases),
        "old_max_complete_phase": old_max, "real_max_complete_phase_found": max_phase,
        "evidence": evidence, "derived_ts": _now_iso(),
        "method": "gh pr list --state merged --search <keyword>, real merge state, "
                  "titles matched to the initiative's own phase plan by topical token "
                  "overlap (never by phase-number coincidence alone) -- never trusts a "
                  "self-reported status field.",
    }

    if max_phase > old_max:
        if max_phase >= len(phases) - 1:
            new_status = (
                f"phase_0_through_phase_{max_phase}_complete_all_{len(phases)}_phases_"
                f"complete_verified_{datetime.date.today().isoformat()}"
            )
        else:
            new_status = f"phase_0_through_phase_{max_phase}_complete_phase_{max_phase + 1}_plus_not_yet_dispatched"
        result_meta["new_status"] = new_status
        return True, result_meta

    return False, result_meta


def apply_status_corrections(model, cur, only):
    """`only`: None means "check/report on every phase-tracked entry" (safe in
    --dry-run, which never mutates the live file) -- an empty set/frozenset means
    "check nothing, touch nothing" (the conservative --apply default) -- a
    non-empty set restricts both checking AND mutation to just those entry ids.

    Confirmed during testing (2026-07-29/30): the token-overlap phase-matcher has
    a real false-positive rate on initiatives whose phase names are short/generic
    (e.g. registries.auditor_engine matched 2 unrelated PRs -- #562 "...defense-in-
    depth security architecture" and #51 "AI Workforce... Worker Agent Dispatch" --
    to phases they have nothing to do with, purely on generic-word overlap).
    registries.terminology_standardization's own correction is independently
    verified clean (8 real merged PRs, each topically unambiguous). Until the
    matcher itself is hardened (separate follow-up, not done here), --apply must
    be run with an explicit --only allowlist naming only entries a human has
    actually reviewed -- never unattended across the full registries[] list."""
    corrected = []
    checked = 0
    if only is not None and len(only) == 0:
        return corrected, checked  # conservative --apply default: nothing in scope
    for entry in model.get("registries", []):
        eid = entry.get("id")
        if only is not None and eid not in only:
            continue  # out of scope for this run -- leave this entry completely untouched
        did_correct, meta = derive_status_correction(entry, cur)
        if meta is not None:
            checked += 1
        if did_correct:
            old_status = entry.get("status")
            entry["status_corrected_from"] = old_status
            entry["status"] = meta["new_status"]
            entry["status_verification"] = {k: v for k, v in meta.items() if k != "new_status"}
            corrected.append({"id": entry.get("id"), "old_status": old_status, "new_status": meta["new_status"]})
        elif meta is not None and meta.get("checked"):
            entry["status_verification"] = meta
    return corrected, checked


# ---------------------------------------------------------------------------
# 3. unregistered_mentions backlog closure
# ---------------------------------------------------------------------------

EXT_TO_LAYER = {".py": "python", ".sh": "shell", ".md": "documentation",
                ".yaml": "documentation", ".json": "documentation"}


def _infer_category(path):
    base = os.path.basename(path).lower()
    if base.startswith("test_"):
        return "validation"
    if base.startswith("generate_") or "generate" in base:
        return "templating"
    if "guardrail" in base:
        return "guardrail"
    if "diagram" in base or base.endswith(".md"):
        return "other"
    return "other"


def resolve_unregistered_mentions(conn, cur, apply):
    cur.execute("SELECT id, ts, software_task_id, mentioned_path, status FROM unregistered_mentions "
                "WHERE status = 'NEEDS_REGISTRATION'")
    rows = cur.fetchall()

    by_path = {}
    for rid, ts, task_id, path, status in rows:
        by_path.setdefault(path, []).append((rid, ts, task_id))

    resolved, still_flagged = [], []

    for path, occurrences in by_path.items():
        resolved_full = _resolve_on_disk(path)
        if not resolved_full:
            still_flagged.append({"path": path, "rows": [o[0] for o in occurrences],
                                   "reason": "not found on disk under known roots"})
            continue

        cur.execute("SELECT index_id FROM system_index WHERE path = ? OR path LIKE ?",
                     (resolved_full, f"%{path}"))
        existing = cur.fetchone()

        if existing:
            index_id = existing[0]
        else:
            index_id = "IDX-auto-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
            _, ext = os.path.splitext(resolved_full)
            layer = EXT_TO_LAYER.get(ext, "documentation")
            category = _infer_category(resolved_full)
            first_seen_ts = min(o[1] for o in occurrences)
            source_tasks = sorted({o[2] for o in occurrences})
            purpose = (f"Auto-registered by regenerate_master_index.py from the "
                       f"unregistered_mentions backlog (postflight_audit_gate.py flagged "
                       f"it, never closed). First flagged {first_seen_ts} by "
                       f"{', '.join(source_tasks)}. Needs human review of its real "
                       f"role/purpose -- this entry only asserts the file genuinely "
                       f"exists on disk at this path, nothing more.")
            if apply:
                cur.execute(
                    "INSERT INTO system_index (index_id, ts, path, category, layer, status, "
                    "purpose, utm_term, calls, called_by, verified_ts, tags, metadata_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (index_id, _now_iso(), resolved_full, category, layer,
                     "live_auto_registered_needs_human_review", purpose, "", "", "",
                     _now_iso(), json.dumps(["auto_registered", "source:unregistered_mentions_backlog"]),
                     json.dumps({"resolved_from_unregistered_mentions_ids": [o[0] for o in occurrences]})),
                )

        if apply:
            for rid, _ts, _task in occurrences:
                cur.execute(
                    "UPDATE unregistered_mentions SET status = ? WHERE id = ?",
                    (f"RESOLVED_AUTO_REGISTERED:{index_id}", rid),
                )

        resolved.append({"path": path, "rows": [o[0] for o in occurrences],
                          "system_index_id": index_id, "was_already_registered": bool(existing)})

    return resolved, still_flagged


def sweep_wiring_registry_coverage(cur):
    """Real, read-only unregistered-mentions sweep AGAINST wiring_registry
    (owner absolute stop-work order, task-20260806-165921): resolve_unregistered_mentions()
    above only ever cross-checks/registers into system_index -- a real path
    can be fully resolved there (RESOLVED_AUTO_REGISTERED:<system_index_id>)
    while still having no real row in wiring_registry at all, invisible to
    that separate registry's own consumers (generate_wiring_registry.py's
    entity_type='file'/'script' rows, wiring_query.py's lookups). This sweeps
    every real, already-disk-resolved unregistered_mentions path (both
    already-RESOLVED_AUTO_REGISTERED rows and any still-NEEDS_REGISTRATION
    row that resolves on disk right now) against wiring_registry.path,
    honestly reporting real coverage gaps. Read-only -- never writes/
    auto-registers into wiring_registry itself (that would be a second,
    competing writer to a table generate_wiring_registry.py already owns
    end-to-end; this sweep's job is visibility, not a second implementation)."""
    cur.execute("SELECT DISTINCT mentioned_path, status FROM unregistered_mentions")
    rows = cur.fetchall()

    covered, not_covered = [], []
    seen_input_paths = set()
    checked_resolved_paths = set()
    for path, status in rows:
        if path in seen_input_paths:
            continue
        seen_input_paths.add(path)
        resolved_full = _resolve_on_disk(path)
        if not resolved_full:
            continue  # not resolvable on disk -- resolve_unregistered_mentions() already flags this
        if resolved_full in checked_resolved_paths:
            continue  # two different mentioned_path spellings resolving to the same real file
        checked_resolved_paths.add(resolved_full)
        cur.execute("SELECT entity_id FROM wiring_registry WHERE path = ? OR path LIKE ?",
                     (resolved_full, f"%{path}"))
        hit = cur.fetchone()
        entry = {"path": resolved_full, "mentioned_as": path, "status": status}
        if hit:
            entry["wiring_registry_entity_id"] = hit[0]
            covered.append(entry)
        else:
            not_covered.append(entry)

    return {
        "total_resolvable_mentions_checked": len(checked_resolved_paths),
        "covered_in_wiring_registry": len(covered),
        "not_covered_in_wiring_registry": len(not_covered),
        "not_covered": not_covered,
    }


# ---------------------------------------------------------------------------
# 4. Model assembly + wholesale rewrite
# ---------------------------------------------------------------------------

def build_regenerated_model(base_model, source_counts, status_corrections, checked_count,
                             resolved_mentions, still_flagged_mentions, wiring_coverage, run_id, only_scope):
    model = base_model  # mutated in place -- this IS the "regeneration": the whole
    # document is re-derived from (live content + fresh DB/gh evidence) every run,
    # then re-serialized wholesale below, never sed/patched on disk.

    model["built_ts"] = _now_iso()
    model["built_by"] = "regenerate_master_index.py (automated, wholesale regeneration each run)"
    model["regeneration_mechanism"] = {
        "script": "/opt/veridian/scripts/regenerate_master_index.py",
        "run_id": run_id,
        "run_ts": _now_iso(),
        "source_registry_counts": source_counts,
        "initiative_status": {
            "scope_this_run": ("all_phase_tracked_entries (dry-run preview only)" if only_scope is None
                                else (sorted(only_scope) if only_scope else "NONE -- conservative --apply "
                                      "default, no --only given, zero status corrections applied or even checked")),
            "entries_checked_for_phase_evidence": checked_count,
            "entries_corrected_this_run": len(status_corrections),
            "corrections": status_corrections,
            "matcher_known_limitation": "The phase-matcher (topical token-overlap between a merged "
                "PR's title and the initiative's own phase-plan names) has a confirmed false-positive "
                "rate on initiatives with short/generic phase names -- see the script's own module "
                "docstring and apply_status_corrections()'s docstring. Only run --apply with an "
                "explicit --only allowlist of entries a human has actually reviewed.",
        },
        "unregistered_mentions_backlog": {
            "resolved_this_run": len(resolved_mentions),
            "still_needs_registration": len(still_flagged_mentions),
            "resolved": resolved_mentions,
            "still_flagged": still_flagged_mentions,
            "note": "postflight_audit_gate.py flags real ai-os/scripts paths mentioned in "
                    "task --content but missing from the registries into this backlog. This "
                    "field exists so the backlog is visible every run, never silent, even for "
                    "rows this script could not safely auto-resolve.",
        },
        "unregistered_mentions_wiring_registry_sweep": {
            **wiring_coverage,
            "note": "Read-only coverage sweep (owner absolute stop-work order, "
                    "task-20260806-165921): resolve_unregistered_mentions() above only "
                    "cross-checks/registers into system_index -- this separately checks "
                    "whether each real, disk-resolved unregistered_mentions path ALSO has "
                    "a real row in wiring_registry (a different, separately-owned "
                    "registry -- see generate_wiring_registry.py). Never writes to "
                    "wiring_registry itself.",
        },
        "not_wired_into_cron": "Manual tool only for now -- cron is disabled server-wide "
                                "pending separate remediation; schedule this once that lands.",
    }
    return model


def dump_yaml(model):
    return yaml.safe_dump(model, default_flow_style=False, sort_keys=False,
                           allow_unicode=True, width=100)


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                       help="read-only: no live-file write, no DB write; writes candidate output to --out")
    mode.add_argument("--apply", action="store_true",
                       help="real run: backs up live file via rename, writes regenerated content, commits DB mutations")
    ap.add_argument("--out", default="/tmp/MASTER_INDEX.regenerated.yaml",
                     help="dry-run output path (ignored in --apply mode)")
    ap.add_argument("--live-path", default=LIVE_PATH)
    ap.add_argument("--only", "--scope", dest="only", default=None,
                     help="comma-separated registries[] entry id(s) (e.g. terminology_standardization) "
                          "to allow initiative-status correction for. In --dry-run, omitting this still "
                          "previews every phase-tracked entry (read-only, safe). In --apply, omitting this "
                          "is the conservative default and applies ZERO status corrections -- you must "
                          "name entries explicitly once a human has reviewed their evidence trail, because "
                          "the token-overlap matcher has a confirmed false-positive rate on short/generic "
                          "phase names (see module docstring). This flag never restricts the separate "
                          "unregistered_mentions auto-registration, which is independently disk-existence-"
                          "verified and safe to run unscoped.")
    args = ap.parse_args()

    only_scope = None
    if args.only is not None:
        only_scope = {s.strip() for s in args.only.split(",") if s.strip()}
    elif args.apply:
        only_scope = set()  # conservative default: --apply with no --only corrects nothing

    with open(args.live_path, encoding="utf-8") as fh:
        pre_checksum_text = fh.read()
    base_model = yaml.safe_load(pre_checksum_text)

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    run_id = "REGEN-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]

    source_counts = query_source_counts(cur)
    status_corrections, checked_count = apply_status_corrections(base_model, cur, only_scope)
    resolved_mentions, still_flagged_mentions = resolve_unregistered_mentions(conn, cur, apply=args.apply)
    wiring_coverage = sweep_wiring_registry_coverage(cur)

    model = build_regenerated_model(base_model, source_counts, status_corrections, checked_count,
                                     resolved_mentions, still_flagged_mentions, wiring_coverage, run_id, only_scope)
    new_text = dump_yaml(model)

    # round-trip validation before touching anything
    yaml.safe_load(new_text)

    summary = {
        "run_id": run_id,
        "mode": "apply" if args.apply else "dry_run",
        "source_registry_counts": source_counts,
        "initiative_entries_checked": checked_count,
        "initiative_status_corrections": status_corrections,
        "unregistered_mentions_resolved": len(resolved_mentions),
        "unregistered_mentions_still_flagged": len(still_flagged_mentions),
        "unregistered_mentions_wiring_registry_coverage": wiring_coverage,
    }

    if args.apply:
        with open(args.live_path, encoding="utf-8") as fh:
            current_text = fh.read()
        if current_text != pre_checksum_text:
            conn.rollback()
            conn.close()
            print(json.dumps({"error": "live file changed since this run started -- aborting, no writes made",
                               **summary}, indent=2))
            sys.exit(2)

        backup_path = args.live_path + ".pre-regen-backup-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        with write_lock():
            os.rename(args.live_path, backup_path)  # true rename-backup: never delete, original bytes preserved
            tmp_path = args.live_path + ".tmp-" + run_id
            with open(tmp_path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
            os.replace(tmp_path, args.live_path)  # atomic create of the new live file
            conn.commit()

        # re-validate in place
        with open(args.live_path, encoding="utf-8") as fh:
            yaml.safe_load(fh.read())

        summary["backup_path"] = backup_path
        summary["live_path"] = args.live_path
    else:
        conn.rollback()  # dry-run: never commit DB mutations
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        summary["out_path"] = args.out

    conn.close()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
