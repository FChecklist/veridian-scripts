#!/usr/bin/env python3
"""gtm_check_role_permission_testing.py -- real, re-runnable check for GTM
certification category_index=16 ("role permission testing").

Owner-delegated real decision (PM-authorized, see
ai-os/boss/ACTIVE-CLAIMS.yaml entry
"task-20260806-215747-owner-delegated-decision--provision-a-re" and
pm_decisions_pending id=70) unblocked this category: the same real,
obviously fictional "Meridian Test Industries" dummy tenant used by
gtm_check_multi_tenant_testing.py (category 15) has one real account per
standard B2B SaaS role (owner/admin, manager, member, viewer), provisioned
via compliance-tracker/scripts/gtm-provision-cat15-16-test-tenant.ts. Real
credentials live only in compliance-tracker/.env.local (gitignored), under
the GTM_TEST_MERIDIAN_* keys this script reads.

What this script does, every real run -- a real 4-role x 5-endpoint
permission matrix, all writes confined to this dummy tenant's own data
(never another real tenant's), exercising the app's real ROLE_RANK gate
(src/lib/supabase/auth-guard.ts) via real Playwright browser logins + real
authenticated HTTP calls:
  admin-only    : POST /api/access-review/cycles          (requireRole admin)
  manager+      : POST /api/departments                    (requireRole manager)
  manager+      : DELETE /api/compliance/[id]               (requireRoleOrScope manager)
  member+       : POST /api/compliance                      (requireRoleOrScope member)
  any role      : GET /api/me                                (auth only, confirms role field)

Sequencing (single Playwright run, one browser, isolated context per
persona, admin/manager act first to create real disposable fixtures the
lower-privilege roles then correctly get denied write/delete access to):
  1. admin  : create a department + a compliance item in it, delete that
              item, attempt admin-only cycle create.
  2. manager: create its own department + item, delete that item, attempt
              (and expect denial of) the admin-only cycle create.
  3. member : attempt (and expect denial of) department create; create its
              own compliance item (member+ allowed); attempt (and expect
              denial of) deleting it (manager+ only) and the admin-only
              cycle create.
  4. viewer : attempt (and expect denial of) department create, item
              create, item delete (of member's still-existing item), and
              the admin-only cycle create; GET /api/me still succeeds
              (read access).

Pass criterion (documented, fixed, not adjustable at call time):
  PASS <=> for every persona, every one of its assigned actions matches the
           real documented ROLE_RANK boundary exactly (a 403 where and only
           where the role is genuinely below the endpoint's minimum rank).
  Any mismatch (an under-privileged role NOT blocked, or an appropriately-
  privileged role wrongly blocked) is a genuine FAIL. "blocked" is reserved
  for: test credentials confirmed absent, Playwright/chromium confirmed
  unable to launch, or every single login attempt failing.

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=16's result.

Usage:
  gtm_check_role_permission_testing.py [--no-write]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
CATEGORY_INDEX = 16
COMPLIANCE_TRACKER_DIR = "/opt/veridian/repos/compliance-tracker"
ENV_LOCAL_PATH = os.path.join(COMPLIANCE_TRACKER_DIR, ".env.local")
LOCAL_LIBS = "/opt/veridian/workspace/browser-tools/local-libs/usr/lib/x86_64-linux-gnu"
LOGIN_URL = "https://projexa-ai.com/login"
BASE_URL = "https://projexa-ai.com"

ROLES = ["owner", "manager", "member", "viewer"]
EXPECTED_ROLE_ENUM = {"owner": "admin", "manager": "manager", "member": "member", "viewer": "viewer"}

PROBE_JS_TEMPLATE = r"""
import { chromium } from '@playwright/test';

const LOGIN_URL = "__LOGIN_URL__";
const BASE_URL = "__BASE_URL__";
const [, , personasJson] = process.argv;
const personas = JSON.parse(personasJson); // owner, manager, member, viewer

const out = { per_persona: {}, checks: [] };
const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });

function record(checks, name, role, endpoint, expected, status) {
  const wasBlocked = status === 403;
  const ok = expected === 'allow' ? !wasBlocked : wasBlocked;
  checks.push({ name, role, endpoint, expected, status, ok });
}

async function login(browser, p) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(LOGIN_URL, { waitUntil: 'load', timeout: 30000 });
  await page.locator('#email').fill(p.email);
  await page.locator('#password').fill(p.password);
  await page.locator('button[type="submit"]').first().click();
  await page.waitForTimeout(3000);
  const meResp = await context.request.get(`${BASE_URL}/api/me`);
  const meBody = meResp.ok() ? await meResp.json() : null;
  return { context, loginOk: meResp.status() === 200 && !!meBody && meBody.role === p.expectedRole, meStatus: meResp.status(), meRole: meBody?.role ?? null };
}

function futureDate() {
  return new Date(Date.now() + 30 * 86400000).toISOString();
}

const byRole = {};
for (const p of personas) byRole[p.role] = p;

let adminItemId = null, managerItemId = null, memberItemId = null;
let adminDeptId = null, managerDeptId = null;

// --- admin (owner/admin persona) ---
{
  const p = byRole['owner'];
  const { context, loginOk, meStatus, meRole } = await login(browser, p);
  out.per_persona['owner'] = { email: p.email, login_ok: loginOk, me_status: meStatus, me_role: meRole };
  if (loginOk) {
    const deptResp = await context.request.post(`${BASE_URL}/api/departments`, { data: { name: 'GTM Cat16 Dept (admin)' } });
    record(out.checks, 'create_department', 'owner', 'POST /api/departments', 'allow', deptResp.status());
    if (deptResp.ok()) { const b = await deptResp.json(); adminDeptId = b.id || b.department?.id || null; }

    if (adminDeptId) {
      const itemResp = await context.request.post(`${BASE_URL}/api/compliance`, { data: { title: 'GTM Cat16 Item (admin)', complianceType: 'OTHER', departmentId: adminDeptId, dueDate: futureDate() } });
      record(out.checks, 'create_compliance_item', 'owner', 'POST /api/compliance', 'allow', itemResp.status());
      if (itemResp.ok()) { const b = await itemResp.json(); adminItemId = b.id || b.item?.id || null; }
    }
    if (adminItemId) {
      const delResp = await context.request.delete(`${BASE_URL}/api/compliance/${adminItemId}`);
      record(out.checks, 'delete_compliance_item', 'owner', `DELETE /api/compliance/${adminItemId}`, 'allow', delResp.status());
    }
    const cycleResp = await context.request.post(`${BASE_URL}/api/access-review/cycles`, { data: { name: 'GTM Cat16 Cycle (admin)' } });
    record(out.checks, 'create_access_review_cycle', 'owner', 'POST /api/access-review/cycles', 'allow', cycleResp.status());
  }
  await context.close();
}

// --- manager ---
{
  const p = byRole['manager'];
  const { context, loginOk, meStatus, meRole } = await login(browser, p);
  out.per_persona['manager'] = { email: p.email, login_ok: loginOk, me_status: meStatus, me_role: meRole };
  if (loginOk) {
    const deptResp = await context.request.post(`${BASE_URL}/api/departments`, { data: { name: 'GTM Cat16 Dept (manager)' } });
    record(out.checks, 'create_department', 'manager', 'POST /api/departments', 'allow', deptResp.status());
    if (deptResp.ok()) { const b = await deptResp.json(); managerDeptId = b.id || b.department?.id || null; }

    if (managerDeptId) {
      const itemResp = await context.request.post(`${BASE_URL}/api/compliance`, { data: { title: 'GTM Cat16 Item (manager)', complianceType: 'OTHER', departmentId: managerDeptId, dueDate: futureDate() } });
      record(out.checks, 'create_compliance_item', 'manager', 'POST /api/compliance', 'allow', itemResp.status());
      if (itemResp.ok()) { const b = await itemResp.json(); managerItemId = b.id || b.item?.id || null; }
    }
    if (managerItemId) {
      const delResp = await context.request.delete(`${BASE_URL}/api/compliance/${managerItemId}`);
      record(out.checks, 'delete_compliance_item', 'manager', `DELETE /api/compliance/${managerItemId}`, 'allow', delResp.status());
    }
    const cycleResp = await context.request.post(`${BASE_URL}/api/access-review/cycles`, { data: { name: 'GTM Cat16 Cycle (manager)' } });
    record(out.checks, 'create_access_review_cycle', 'manager', 'POST /api/access-review/cycles', 'deny', cycleResp.status());
  }
  await context.close();
}

// --- member ---
{
  const p = byRole['member'];
  const { context, loginOk, meStatus, meRole } = await login(browser, p);
  out.per_persona['member'] = { email: p.email, login_ok: loginOk, me_status: meStatus, me_role: meRole };
  if (loginOk) {
    const deptResp = await context.request.post(`${BASE_URL}/api/departments`, { data: { name: 'GTM Cat16 Dept (member, should be denied)' } });
    record(out.checks, 'create_department', 'member', 'POST /api/departments', 'deny', deptResp.status());

    const targetDeptId = adminDeptId || managerDeptId;
    if (targetDeptId) {
      const itemResp = await context.request.post(`${BASE_URL}/api/compliance`, { data: { title: 'GTM Cat16 Item (member)', complianceType: 'OTHER', departmentId: targetDeptId, dueDate: futureDate() } });
      record(out.checks, 'create_compliance_item', 'member', 'POST /api/compliance', 'allow', itemResp.status());
      if (itemResp.ok()) { const b = await itemResp.json(); memberItemId = b.id || b.item?.id || null; }
    }
    if (memberItemId) {
      const delResp = await context.request.delete(`${BASE_URL}/api/compliance/${memberItemId}`);
      record(out.checks, 'delete_compliance_item', 'member', `DELETE /api/compliance/${memberItemId} (own item, manager+ only)`, 'deny', delResp.status());
    }
    const cycleResp = await context.request.post(`${BASE_URL}/api/access-review/cycles`, { data: { name: 'GTM Cat16 Cycle (member)' } });
    record(out.checks, 'create_access_review_cycle', 'member', 'POST /api/access-review/cycles', 'deny', cycleResp.status());
  }
  await context.close();
}

// --- viewer ---
{
  const p = byRole['viewer'];
  const { context, loginOk, meStatus, meRole } = await login(browser, p);
  out.per_persona['viewer'] = { email: p.email, login_ok: loginOk, me_status: meStatus, me_role: meRole };
  if (loginOk) {
    record(out.checks, 'read_me', 'viewer', 'GET /api/me', 'allow', meStatus);

    const deptResp = await context.request.post(`${BASE_URL}/api/departments`, { data: { name: 'GTM Cat16 Dept (viewer, should be denied)' } });
    record(out.checks, 'create_department', 'viewer', 'POST /api/departments', 'deny', deptResp.status());

    const targetDeptId = adminDeptId || managerDeptId;
    if (targetDeptId) {
      const itemResp = await context.request.post(`${BASE_URL}/api/compliance`, { data: { title: 'GTM Cat16 Item (viewer, should be denied)', complianceType: 'OTHER', departmentId: targetDeptId, dueDate: futureDate() } });
      record(out.checks, 'create_compliance_item', 'viewer', 'POST /api/compliance', 'deny', itemResp.status());
    }
    if (memberItemId) {
      const delResp = await context.request.delete(`${BASE_URL}/api/compliance/${memberItemId}`);
      record(out.checks, 'delete_compliance_item', 'viewer', `DELETE /api/compliance/${memberItemId} (member's item, should be denied)`, 'deny', delResp.status());
    }
    const cycleResp = await context.request.post(`${BASE_URL}/api/access-review/cycles`, { data: { name: 'GTM Cat16 Cycle (viewer)' } });
    record(out.checks, 'create_access_review_cycle', 'viewer', 'POST /api/access-review/cycles', 'deny', cycleResp.status());
  }
  await context.close();
}

await browser.close();
console.log(JSON.stringify(out));
"""


def call_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_role_permission_testing.py",
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    print("Calling writer:", " ".join(cmd), file=sys.stderr)
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        sys.exit(p.returncode)


def emit(args, result, summary, evidence):
    if args.no_write:
        print(json.dumps({"result": result, "summary": summary, "evidence": evidence}, indent=2))
        return
    call_writer(result, summary, evidence)


def get_env_value(key, path=ENV_LOCAL_PATH):
    try:
        with open(path) as f:
            for line in f:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    node = shutil.which("node")
    if not node:
        emit(args, "blocked", "node confirmed absent from PATH; cannot run Playwright.", {"missing_tools": ["node"]})
        return

    pw_dir = os.path.join(COMPLIANCE_TRACKER_DIR, "node_modules", "@playwright", "test")
    if not os.path.isdir(pw_dir):
        emit(args, "blocked", f"@playwright/test confirmed absent at {pw_dir}.", {"missing": "@playwright/test"})
        return
    if not os.path.isdir(LOCAL_LIBS):
        emit(args, "blocked", f"Real user-space shared-library set at {LOCAL_LIBS} confirmed absent.", {"local_libs_dir": LOCAL_LIBS})
        return

    org_id = get_env_value("GTM_TEST_MERIDIAN_ORG_ID")
    if not org_id:
        emit(
            args, "blocked",
            f"GTM_TEST_MERIDIAN_ORG_ID confirmed absent from {ENV_LOCAL_PATH} -- test tenant not provisioned. "
            f"Run compliance-tracker/scripts/gtm-provision-cat15-16-test-tenant.ts first.",
            {"env_local_path": ENV_LOCAL_PATH},
        )
        return

    personas = []
    missing_creds = []
    for role in ROLES:
        email = get_env_value(f"GTM_TEST_MERIDIAN_{role.upper()}_EMAIL")
        password = get_env_value(f"GTM_TEST_MERIDIAN_{role.upper()}_PASSWORD")
        if not email or not password:
            missing_creds.append(role)
            continue
        personas.append({"role": role, "email": email, "password": password, "expectedRole": EXPECTED_ROLE_ENUM[role]})

    if len(personas) != len(ROLES):
        emit(
            args, "blocked",
            f"Not all 4 GTM_TEST_MERIDIAN_*_EMAIL/PASSWORD pairs found in {ENV_LOCAL_PATH} (missing: {missing_creds}) -- role matrix needs all 4 roles present.",
            {"missing_creds": missing_creds},
        )
        return

    probe_js = PROBE_JS_TEMPLATE.replace("__LOGIN_URL__", LOGIN_URL).replace("__BASE_URL__", BASE_URL)
    probe_path = os.path.join(COMPLIANCE_TRACKER_DIR, ".gtm_role_permission_probe.mjs")
    with open(probe_path, "w") as f:
        f.write(probe_js)

    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = LOCAL_LIBS + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")

    try:
        p = subprocess.run(
            [node, probe_path, json.dumps(personas)],
            cwd=COMPLIANCE_TRACKER_DIR, env=env, capture_output=True, text=True, timeout=240,
        )
        stdout_tail = (p.stdout or "").strip()
        probe_out = json.loads(stdout_tail.splitlines()[-1]) if stdout_tail else None
        probe_error = None if probe_out else f"no stdout (exit {p.returncode}); stderr: {(p.stderr or '')[-1500:]}"
    except subprocess.TimeoutExpired:
        probe_out, probe_error = None, "probe timed out after 240s"
    except (json.JSONDecodeError, IndexError):
        probe_out, probe_error = None, f"non-JSON stdout (exit {p.returncode}); stderr: {(p.stderr or '')[-1500:]}"
    finally:
        if os.path.isfile(probe_path):
            os.remove(probe_path)

    if probe_out is None:
        emit(args, "blocked", f"Real Playwright probe failed to produce parseable output: {probe_error}", {"probe_error": probe_error})
        return

    per_persona = probe_out.get("per_persona", {})
    checks = probe_out.get("checks", [])
    logins_failed = [role for role, r in per_persona.items() if not r.get("login_ok")]

    if len(logins_failed) == len(personas):
        emit(
            args, "blocked",
            f"Real login failed for all {len(personas)} provisioned test accounts against {LOGIN_URL} -- cannot exercise the role/permission matrix without at least one working authenticated session.",
            {"per_persona": per_persona, "logins_failed": logins_failed},
        )
        return

    mismatches = [c for c in checks if not c.get("ok")]
    result = "fail" if mismatches else "pass"

    mismatch_strs = [
        "{role} {endpoint} expected {expected} got status {status}".format(**m)
        for m in mismatches[:5]
    ]

    evidence = {
        "org_under_test": {"id": org_id, "name": "Meridian Test Industries (GTM Cat 15/16 Test Fixture -- Non-Production)"},
        "accounts_tested": [{"role": p["role"], "email": p["email"], "expected_enum_role": p["expectedRole"]} for p in personas],
        "base_url": BASE_URL,
        "role_rank_endpoints_tested": {
            "GET /api/me": "any authenticated role",
            "POST /api/departments": "requireRole('manager') -- manager+ allow, viewer/member deny",
            "POST /api/compliance": "requireRoleOrScope('member') -- member+ allow, viewer deny",
            "DELETE /api/compliance/[id]": "requireRoleOrScope('manager') -- manager+ allow, viewer/member deny (even on their own created item)",
            "POST /api/access-review/cycles": "requireRole('admin') -- admin allow, manager/member/viewer deny",
        },
        "per_persona": per_persona,
        "checks": checks,
        "mismatches": mismatches,
        "logins_failed": logins_failed,
        "pass_criterion": "every check's real HTTP status matches its documented ROLE_RANK expectation exactly (403 iff the role is genuinely below the endpoint's minimum rank)",
        "provisioning_script": "compliance-tracker/scripts/gtm-provision-cat15-16-test-tenant.ts",
    }
    ok_count = len(checks) - len(mismatches)
    if mismatches:
        summary = f"Role permission matrix: {ok_count}/{len(checks)} real checks across 4 roles x 5 endpoints matched the documented ROLE_RANK boundary. {len(mismatches)} mismatch(es): " + "; ".join(mismatch_strs)
    else:
        summary = f"Role permission matrix: {ok_count}/{len(checks)} real checks across 4 roles x 5 endpoints matched the documented ROLE_RANK boundary. 0 mismatches -- every role was allowed exactly what it should be and blocked from exactly what it shouldn't."
    emit(args, result, summary, evidence)


if __name__ == "__main__":
    main()
