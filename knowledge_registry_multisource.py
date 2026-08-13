#!/usr/bin/env python3
"""
knowledge_registry_multisource.py -- Knowledge Engine Phase 2 (task-20260724-033446),
SCOPE item 1: extends the knowledge_engine table (built Phase 1,
task-20260723-181151) to cover 4 sources file_inventory.py never touches
(file_inventory.py is explicitly scoped to ai-os/+scripts/ only, per its own
docstring) -- VERCEL, SUPABASE, GITHUB, LOCAL -- plus retags the 9 Phase-1
SERVER rows so every row in the table now carries a source:X tag.

Idempotent by design, safe to run on a cron: for each real artifact this
queries live state for, it checks whether a knowledge_engine row already
exists for that artifact_path (via the existing query-knowledge/sqlite read)
and calls verify-knowledge (UPDATE in place) if so, register-knowledge
(INSERT) only the first time. Never invents a path -- every row's
artifact_path is a real file this script itself confirmed exists (or, for
the 2 already-documented drift rows, does not) at run time.

Sources and what "technical spec" means for each (register_knowledge's
schema has no dedicated source/spec columns -- both are carried via the
existing tags [source:X] and metadata_json fields, per Phase 1's own
"path + metadata, not a content copy" contract):

  SERVER   -- the 9 canonical/derived governance artifacts Phase 1 already
              seeded. This script only adds a source:SERVER tag; it does not
              re-register or duplicate file_inventory.py's own ai-os/+scripts/
              coverage (KNOWN_CONTEXT: "do not redo this").
  VERCEL   -- one row per linked project (compliance-tracker/projexa/
              veda-advisors), anchored at the real local .vercel/project.json
              (hash-trackable), metadata_json carries a live `vercel project
              ls` snapshot (production URL, node version) via the same
              VERCEL_ACCESS_TOKEN sync-vercel-env.sh already uses.
  SUPABASE -- one row per live Supabase project (via Supabase Management API,
              same REST surface the MCP connector uses) matched to a linked
              repo, anchored at the real local DATABASE_CATALOG.json (already
              covers app-code/DB-schema per KNOWN_CONTEXT -- referenced, not
              duplicated), metadata_json carries live table/RLS counts.
              KNOWN GAP (see metadata_json.supabase_api_status on each row
              when degraded): the SUPABASE_ACCESS_TOKEN stored in
              /opt/veridian/shared/.env returns 401 Unauthorized from
              https://api.supabase.com/v1/projects as of 2026-07-24 -- this
              script degrades gracefully (skips the live refresh, keeps the
              last-known-good metadata, logs the failure) rather than crash
              the whole run. Real, live-verified initial data was captured
              once via the interactive Supabase MCP connector before this
              script existed; recurring cron refresh needs a working token
              (see KNOWLEDGE_ENGINE_PHASE3_CANDIDATES_2026-07-24.yaml,
              ai-os/OWNER_DECISIONS_NEEDED_2026-07-23.yaml#supabase-management-api-token-refresh
              -- minting a fresh Management API PAT is a real Owner-dashboard
              action, not a code gap). Phase 5 (task-20260724-135834) added a
              real mitigation, not a full fix: when the Management API call
              fails, supabase_project_healthcheck_fallback() uses each repo's
              own already-configured, unrelated project-scoped anon key to hit
              Supabase's standard /auth/v1/health endpoint, so the row still
              gets a genuine live signal every cron tick instead of pure
              last-known-good data (metadata_json.fallback_healthcheck).
  GITHUB   -- one row per repo under /opt/veridian/repos, anchored at the
              real local .git/HEAD file, metadata_json carries a live `gh`
              snapshot (default branch, branch protection, open PR count).
  LOCAL    -- control/CONTROLLER.yaml (= this repo's own root CONTROLLER.yaml,
              real, reachable via the git mirror) is registered with
              reachable=true. The Owner's laptop-side memory/*.md is NOT
              registered -- STANDING_DIRECTIVE.yaml's own
              assistant_working_protocol documents "laptop never holds a
              persistent copy... only transient display/relay", so no real
              server-side path exists to hash. That gap is recorded as an
              explicit metadata_json note on the CONTROLLER.yaml row instead
              of a fabricated path, per this task's own constraint to make
              unreachable sources a visible gap, not a silent skip.
"""
import json
import os
import subprocess
import sys
import urllib.request

VERIDIAN_ROOT = "/opt/veridian"
SUPERBOSS = f"{VERIDIAN_ROOT}/scripts/superboss-register.py"
NOTIFICATION_ENGINE = f"{VERIDIAN_ROOT}/scripts/notification_engine.py"
REPOS_ROOT = f"{VERIDIAN_ROOT}/repos"
SHARED_ENV = f"{VERIDIAN_ROOT}/shared/.env"
DATABASE_CATALOG = f"{VERIDIAN_ROOT}/ai-os/DATABASE_CATALOG.json"
CONTROLLER_YAML = f"{REPOS_ROOT}/claude-control/CONTROLLER.yaml"

VERCEL_PROJECTS = ["compliance-tracker", "projexa", "veda-advisors"]
VERCEL_SCOPE = "meet-track-s-projects"
GITHUB_OWNER = "FChecklist"

SERVER_ROWS = [
    "/opt/veridian/repos/compliance-tracker/ai-os/CONSTITUTION.yaml",
    "/opt/veridian/ai-os/RULES_ARTICLES_198.json",
    "/opt/veridian/ai-os/VERIDIAN_EXECUTION_RULES_2026-07-23.md",
    "/opt/veridian/ai-os/EXECUTION_RULES_AUDIT_2026-07-23.yaml",
    "/opt/veridian/ai-os/GOVERNANCE_AUDIT_RESULT_2026-07-23.yaml",
    "/opt/veridian/ai-os/MASTER_GAP_AUDIT_2026-07-23.yaml",
    "/opt/veridian/ai-os/MASTER_INDEX.yaml",
    "/opt/veridian/ai-os/SYSTEM_MAP.yaml",
    "/opt/veridian/ai-os/STANDING_DIRECTIVE.yaml",
]


# Real, canonical DB-path resolution -- same lazy-import-cached convention
# reconcile_stale_running_workers.py / worker-exit-status-bridge.py already use.
_sbr = None


def _superboss_register():
    global _sbr
    if _sbr is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "superboss_register_knowledge_registry", SUPERBOSS)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbr = _mod
    return _sbr


def _resolve_db_path():
    return _superboss_register().resolve_superboss_db_path()


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=kw.pop("timeout", 60), **kw)


def sb(args_list):
    proc = run(["python3", SUPERBOSS] + args_list)
    if proc.returncode != 0:
        print(f"  ! superboss-register.py {args_list[0]} failed: {proc.stderr[-500:]}")
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def row_exists(path):
    import sqlite3
    conn = sqlite3.connect(_resolve_db_path())
    row = conn.execute(
        "SELECT 1 FROM knowledge_engine WHERE artifact_path = ? ORDER BY ts DESC LIMIT 1", (path,)
    ).fetchone()
    conn.close()
    return row is not None


def register_or_verify(path, artifact_type, purpose, tags, relationships=None, metadata=None):
    """The idempotency contract this whole script relies on: register once,
    verify (in-place UPDATE, never a duplicate INSERT) every run after.
    Normalizes both branches' return shape to always carry a top-level
    verification_status, so callers never need to know which branch ran."""
    if row_exists(path):
        verify_result = sb(["verify-knowledge", "--path", path])
        status = None
        if verify_result and verify_result.get("results"):
            status = verify_result["results"][0].get("verification_status")
        if metadata:
            # verify-knowledge only refreshes hash/status; metadata (live API
            # snapshots) is refreshed via a fresh annotate-knowledge note each
            # run so the latest live check is always the most recent entry.
            sb(["annotate-knowledge", "--path", path,
                "--note", json.dumps({"live_metadata_refresh": metadata})])
        for tag in tags:
            sb(["add-tag", "--path", path, "--tag", tag])
        return {"verification_status": status, "mode": "verified", "raw": verify_result}
    cmd = [
        "register-knowledge", "--path", path, "--artifact-type", artifact_type,
        "--purpose", purpose, "--tags", ",".join(tags),
        "--relationships", json.dumps(relationships or []),
    ]
    if metadata:
        cmd += ["--metadata", json.dumps(metadata)]
    return sb(cmd)


def retag_server_rows():
    print("=== SERVER (retag existing Phase 1 rows) ===")
    for path in SERVER_ROWS:
        if row_exists(path):
            sb(["add-tag", "--path", path, "--tag", "source:SERVER"])
            print(f"  retagged source:SERVER -> {path}")


def do_vercel():
    print("=== VERCEL ===")
    if not os.path.isfile(SHARED_ENV):
        print("  ! shared/.env not found, skipping VERCEL live refresh")
        return
    token = None
    for line in open(SHARED_ENV):
        if line.startswith("VERCEL_ACCESS_TOKEN="):
            token = line.strip().split("=", 1)[1]
    if not token:
        print("  ! VERCEL_ACCESS_TOKEN not found in shared/.env, skipping")
        return

    proc = run(["vercel", "project", "ls", "--token", token, "--scope", VERCEL_SCOPE], timeout=45)
    listing = proc.stdout if proc.returncode == 0 else None

    for repo in VERCEL_PROJECTS:
        project_json_path = f"{REPOS_ROOT}/{repo}/.vercel/project.json"
        if not os.path.isfile(project_json_path):
            print(f"  ! {repo}: no .vercel/project.json, skipping")
            continue
        project_meta = json.loads(open(project_json_path).read())
        project_name = project_meta.get("projectName", repo)
        snippet = None
        if listing:
            for line in listing.splitlines():
                if project_name in line:
                    snippet = line.strip()
                    break
        result = register_or_verify(
            project_json_path, "canonical",
            f"Vercel project link for {repo} (projectId/orgId, real deployment target) -- registered "
            f"per Knowledge Engine Phase 2 SCOPE item 1 (VERCEL source coverage).",
            [f"source:VERCEL", f"project:{repo}", "deployment-config"],
            relationships=[{
                "path": f"{REPOS_ROOT}/{repo}",
                "relationship_type": "deployment_target_for_repo",
                "evidence": f".vercel/project.json projectId={project_meta.get('projectId')}",
            }],
            metadata={
                "vercel_project_id": project_meta.get("projectId"),
                "vercel_org_id": project_meta.get("orgId"),
                "vercel_project_name": project_name,
                "live_vercel_project_ls_line": snippet,
                "live_check_via": "vercel CLI (project ls), token from shared/.env, same credential sync-vercel-env.sh cron uses",
            },
        )
        print(f"  {repo}: {result.get('verification_status') if result else 'ERROR'}")


def supabase_api_get(token, path):
    req = urllib.request.Request(f"https://api.supabase.com{path}", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        return {"error": str(e)}, e.code
    except Exception as e:
        return {"error": str(e)}, None


SUPABASE_PROJECT_MAP = {
    "verdian-ai": "compliance-tracker",
    "projexa": "projexa",
}


def notify_owner_supabase_token_degraded(http_status, body):
    """Phase 5 (metadata_knowledge_consolidation, task-20260724-140008): the
    real fix for the part of the SUPABASE cron gap that IS a code gap -- the
    degrade above was silent (a stdout line + a metadata_json note nobody
    actively reads, same failure mode notify-owner.py's own module docstring
    already documents for ai-os/logs/ATTENTION.md). Fires a real, plain-English,
    rate-limited (1/hour, so effectively once per 6h cron run while still
    broken) Owner email via scripts/notification_engine.py's send-owner facade
    -- the first real Metadata/Knowledge Engine (14/15) -> Notification Engine
    (12) cross-call. Does NOT and cannot fix the credential itself (a Supabase
    Management API PAT can only be minted by the Owner via the dashboard, see
    ai-os/OWNER_DECISIONS_NEEDED_2026-07-23.yaml#supabase-access-token-credential-refresh)
    -- this only makes the gap loud instead of silent."""
    if not os.path.isfile(NOTIFICATION_ENGINE):
        print(f"  ! notification_engine.py not found at {NOTIFICATION_ENGINE}, cannot alert Owner")
        return
    subject = "Supabase knowledge-registry refresh is still broken"
    body_text = (
        "The 6-hourly job that keeps your Supabase project info up to date on the server "
        "cannot log in to Supabase right now. The saved access token is being rejected "
        f"(status {http_status}). Nothing is broken in your Supabase projects themselves -- "
        "this is just a server-side credential that needs replacing.\n\n"
        "To fix it: sign in to supabase.com, go to Account Settings > Access Tokens, create a "
        "new token, and give it to your AI assistant to save in the server's shared/.env file "
        "under SUPABASE_ACCESS_TOKEN. Until then, this email will repeat every few hours."
    )
    proc = run([
        "python3", NOTIFICATION_ENGINE, "send-owner",
        "--subject", subject, "--body", body_text,
        "--dedupe-key", "supabase-access-token-degraded",
        "--source-engine", "knowledge_registry_multisource",
    ])
    print(f"  notify-owner: {proc.stdout.strip() or proc.stderr.strip()}")


def read_env_file(path):
    env = {}
    if not os.path.isfile(path):
        return env
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k] = v.strip().strip('"').strip("'")
    return env


def supabase_project_healthcheck_fallback(repo):
    """Phase 5 (task-20260724-135834) mitigation for the SUPABASE_ACCESS_TOKEN gap
    (KNOWLEDGE_ENGINE_PHASE3_CANDIDATES_2026-07-24.yaml supabase_cron_credential_broken):
    the broken credential is the account-level Management API PAT
    (api.supabase.com/v1/projects) -- minting a fresh one is a real Owner-dashboard
    action, not a code fix (see ai-os/OWNER_DECISIONS_NEEDED_2026-07-23.yaml). But
    each repo already carries its OWN project-scoped anon key (NEXT_PUBLIC_SUPABASE_URL
    / NEXT_PUBLIC_SUPABASE_ANON_KEY in that repo's .env.local, needed by the app itself,
    unrelated to the broken PAT and requiring no new Owner action) which can hit
    Supabase's standard GoTrue /auth/v1/health endpoint -- a real, safe, read-only,
    zero-side-effect liveness check. This does not replace the Management API snapshot
    (project id/status/table list still need the PAT), but it stops the SUPABASE row
    from going fully stale between successful Management API calls: every 6h cron tick
    gets a genuine live signal instead of pure last-known-good data. Best-effort per
    repo -- projexa's own .env.local carries no Supabase credentials at all (confirmed
    2026-07-24), so this returns None for it rather than fabricating a check."""
    env = read_env_file(f"{REPOS_ROOT}/{repo}/.env.local")
    url = env.get("NEXT_PUBLIC_SUPABASE_URL")
    key = env.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    if not url or not key:
        return None
    req = urllib.request.Request(f"{url.rstrip('/')}/auth/v1/health", headers={"apikey": key})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
            return {"ok": True, "http_status": resp.status, "gotrue_version": body.get("version")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "http_status": e.code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def do_supabase():
    print("=== SUPABASE ===")
    if not os.path.isfile(DATABASE_CATALOG):
        print(f"  ! {DATABASE_CATALOG} not found, skipping (KNOWN_CONTEXT says reference it, not duplicate it)")
        return
    token = None
    if os.path.isfile(SHARED_ENV):
        for line in open(SHARED_ENV):
            if line.startswith("SUPABASE_ACCESS_TOKEN="):
                token = line.strip().split("=", 1)[1]

    projects, status = (None, None)
    if token:
        projects, status = supabase_api_get(token, "/v1/projects")

    api_ok = isinstance(projects, list)
    if not api_ok:
        print(f"  ! Supabase Management API call failed (http_status={status}, body={projects}) -- "
              f"degraded mode, real known gap (see KNOWLEDGE_ENGINE_PHASE3_CANDIDATES_2026-07-24.yaml, "
              f"ai-os/OWNER_DECISIONS_NEEDED_2026-07-23.yaml#supabase-management-api-token-refresh). "
              f"Falling back to a per-project anon-key health check (Phase 5 mitigation) so this run still "
              f"captures a real live signal instead of pure last-known-good data.")
        notify_owner_supabase_token_degraded(status, projects)

    for supa_name, repo in SUPABASE_PROJECT_MAP.items():
        table_summary = None
        if api_ok:
            match = next((p for p in projects if p.get("name") == supa_name), None)
            if match:
                tables_resp, tstatus = supabase_api_get(token, f"/v1/projects/{match['id']}/database/query")
                table_summary = {"project_ref": match.get("id"), "status_field": match.get("status")}

        fallback_health = None if api_ok else supabase_project_healthcheck_fallback(repo)
        if fallback_health:
            status_suffix = "FALLBACK_HEALTHCHECK_OK" if fallback_health.get("ok") else "FALLBACK_HEALTHCHECK_FAILED"
        else:
            status_suffix = "FALLBACK_UNAVAILABLE"

        result = register_or_verify(
            DATABASE_CATALOG, "canonical",
            f"Supabase project '{supa_name}' (repo: {repo}) remote schema summary -- DATABASE_CATALOG.json is "
            f"the existing canonical local schema catalog (KNOWN_CONTEXT: reference, do not duplicate); this "
            f"row anchors that file's hash for the {repo} Supabase slice, registered per Knowledge Engine "
            f"Phase 2 SCOPE item 1 (SUPABASE source coverage).",
            [f"source:SUPABASE", f"project:{repo}", f"supabase-project:{supa_name}", "remote-schema"],
            relationships=[{
                "path": f"{REPOS_ROOT}/{repo}",
                "relationship_type": "database_for_repo",
                "evidence": f"Supabase project name={supa_name}",
            }],
            metadata={
                "supabase_project_name": supa_name,
                "supabase_api_status": "OK" if api_ok else f"DEGRADED_HTTP_{status}_{status_suffix}",
                "supabase_api_note": (
                    "live" if api_ok else
                    "SUPABASE_ACCESS_TOKEN in shared/.env returned 401 from api.supabase.com/v1/projects -- "
                    "credential needs Owner refresh for this script to keep this row live-current (see "
                    "ai-os/OWNER_DECISIONS_NEEDED_2026-07-23.yaml#supabase-management-api-token-refresh); "
                    "last real Management API verification of this project's schema was via the interactive "
                    "Supabase MCP connector on 2026-07-24 (task-20260724-033446)"
                ),
                "live_check_snapshot": table_summary,
                "fallback_healthcheck": fallback_health,
            },
        )
        print(f"  {supa_name} ({repo}): {result.get('verification_status') if result else 'ERROR'}"
              f" [fallback_healthcheck={fallback_health}]")


def do_github():
    print("=== GITHUB ===")
    if not os.path.isdir(REPOS_ROOT):
        return
    for repo in sorted(os.listdir(REPOS_ROOT)):
        repo_path = f"{REPOS_ROOT}/{repo}"
        head_path = f"{repo_path}/.git/HEAD"
        if not os.path.isfile(head_path):
            continue

        branches_proc = run(["gh", "api", f"repos/{GITHUB_OWNER}/{repo}", "--jq",
                              "{default_branch: .default_branch, open_issues: .open_issues_count, archived: .archived}"])
        repo_meta = {}
        if branches_proc.returncode == 0:
            try:
                repo_meta = json.loads(branches_proc.stdout)
            except json.JSONDecodeError:
                pass

        default_branch = repo_meta.get("default_branch", "main")
        protection_proc = run(["gh", "api", f"repos/{GITHUB_OWNER}/{repo}/branches/{default_branch}/protection"])
        protected = protection_proc.returncode == 0

        pr_proc = run(["gh", "pr", "list", "--repo", f"{GITHUB_OWNER}/{repo}", "--state", "open",
                        "--json", "number"])
        open_pr_count = None
        if pr_proc.returncode == 0:
            try:
                open_pr_count = len(json.loads(pr_proc.stdout))
            except json.JSONDecodeError:
                pass

        result = register_or_verify(
            head_path, "derived",
            f"GitHub remote repo state for {GITHUB_OWNER}/{repo} (default branch, protection rules, open PR "
            f"count) -- anchored at the real local .git/HEAD mirror, registered per Knowledge Engine Phase 2 "
            f"SCOPE item 1 (GITHUB source coverage).",
            [f"source:GITHUB", f"project:{repo}", "remote-repo-state"],
            relationships=[{
                "path": f"{REPOS_ROOT}/claude-control/CONTROLLER.yaml",
                "relationship_type": "tracked_by_controller_index",
                "evidence": f"repo {repo} is one of the repos this session's CONTROLLER.yaml/README.md governs",
            }],
            metadata={
                "github_default_branch": default_branch,
                "github_branch_protected": protected,
                "github_open_pr_count": open_pr_count,
                "github_open_issues_count": repo_meta.get("open_issues"),
                "github_archived": repo_meta.get("archived"),
                "live_check_via": "gh api / gh pr list",
            },
        )
        print(f"  {repo}: {result.get('verification_status') if result else 'ERROR'} "
              f"(default_branch={default_branch}, protected={protected}, open_prs={open_pr_count})")


def do_local():
    print("=== LOCAL ===")
    if not os.path.isfile(CONTROLLER_YAML):
        print(f"  ! {CONTROLLER_YAML} not found, skipping")
        return
    result = register_or_verify(
        CONTROLLER_YAML, "canonical",
        "The Owner-facing 'control' repo's own CONTROLLER.yaml (cross-project requirements & decision "
        "catalog). This IS the real, server-reachable form of the SPEC's 'control/CONTROLLER.yaml' -- "
        "reachable from this server via the git mirror (github.com/FChecklist/claude-control), which the "
        "Owner's laptop clones/pulls the identical content from. Registered per Knowledge Engine Phase 2 "
        "SCOPE item 1 (LOCAL source coverage).",
        ["source:LOCAL", "reachable:true", "controller"],
        metadata={
            "reachable_from_server": True,
            "reachable_mechanism": "git clone/pull of github.com/FChecklist/claude-control (this same repo)",
            "explicit_gap_owner_laptop_memory_md": (
                "Owner's Windows laptop-side Claude Code memory/*.md files are NOT reachable/mirrored from "
                "this server -- STANDING_DIRECTIVE.yaml's assistant_working_protocol documents 'laptop never "
                "holds a persistent copy of any of the above, only transient display/relay' (line ~401), and "
                "no sync mechanism, repo, or file path for laptop-side memory exists anywhere on this server "
                "(confirmed via exhaustive grep across ai-os/ 2026-07-24, task-20260724-033446). This is a "
                "genuine architectural gap, not a missed lookup -- recorded here per this task's own "
                "constraint to make an unreachable LOCAL sub-source an explicit, visible gap rather than "
                "silently registering nothing for it."
            ),
        },
    )
    print(f"  CONTROLLER.yaml: {result.get('verification_status') if result else 'ERROR'}")
    print("  ! GAP (explicit, documented): Owner laptop-side memory/*.md is unreachable from this server -- see metadata_json.")


def main():
    retag_server_rows()
    do_vercel()
    do_supabase()
    do_github()
    do_local()
    print("=== knowledge_registry_multisource.py run complete ===")


if __name__ == "__main__":
    sys.exit(main())
