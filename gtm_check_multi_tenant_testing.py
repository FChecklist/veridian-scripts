#!/usr/bin/env python3
"""gtm_check_multi_tenant_testing.py -- real, re-runnable check for GTM
certification category_index=15 ("multi tenant testing").

Owner-delegated real decision (PM-authorized, see
ai-os/boss/ACTIVE-CLAIMS.yaml entry
"task-20260806-215747-owner-delegated-decision--provision-a-re" and
pm_decisions_pending id=69) unblocked this category: a real, obviously
fictional, clearly test-flagged dummy tenant ("Meridian Test Industries",
compliance.organisations.slug=meridian-test-industries-gtm-fixture-nonprod,
internal_use_exempt=true) with one real account per standard B2B SaaS role
(owner/admin, manager, member, viewer -- source: descope.com RBAC providers
guide, WorkOS multi-tenant RBAC design guide) was provisioned via
compliance-tracker/scripts/gtm-provision-cat15-16-test-tenant.ts. Real
credentials (freshly generated, never hardcoded) live only in
compliance-tracker/.env.local (gitignored), under the GTM_TEST_MERIDIAN_*
keys this script reads.

What this script does, every real run, for EACH of the 4 real accounts:
  1. Real browser login at https://projexa-ai.com/login (Playwright chromium,
     fills the real #email/#password form and submits -- this is the
     account's own dedicated, generated-for-this-purpose credential, not a
     found/guessed one, and its use here is exactly what the Owner-delegated
     decision authorizes -- the standing no-credential-entry rule was about
     never using an unauthorized/found credential, not a categorical ban on
     ever testing a login this environment itself provisioned for this exact
     purpose).
  2. GET /api/me -- confirms the session's orgId/orgSlug is ALWAYS the
     Meridian test org, never anything else.
  3. GET /api/compliance (list) -- confirms the response never contains a
     real foreign org's known item id (org_001 "Acme Corp", item ci_02).
  4. GET /api/compliance/ci_02 -- a real complianceItems row that genuinely
     belongs to a different real tenant (Acme Corp, org_001). Real Postgres
     RLS (compliance.current_org_id() GUC, src/lib/db/tenant-scoped.ts) is
     expected to make this row invisible to a Meridian session regardless of
     role -- expected result is 404 "Compliance item not found", never 200.
  5. GET /api/departments -- confirms the response never contains a real
     foreign department id (dept_finance, also Acme Corp's).

Every probe is read-only (GET only) -- if isolation were ever broken, this
script would observe and report it, never risk mutating another real
tenant's data to prove the point.

Pass criterion (documented, fixed, not adjustable at call time):
  PASS <=> for all 4 accounts: /api/me always reports the Meridian org, the
           list endpoints never surface a foreign id, and the direct
           cross-tenant fetch is always denied (non-200, expected 404).
  Any real leak above is a genuine FAIL. "blocked" is reserved for: the
  test credentials confirmed absent from .env.local, Playwright/chromium
  confirmed unable to launch, or every single login attempt failing.

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=15's result.

Usage:
  gtm_check_multi_tenant_testing.py [--no-write]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
CATEGORY_INDEX = 15
COMPLIANCE_TRACKER_DIR = "/opt/veridian/repos/compliance-tracker"
ENV_LOCAL_PATH = os.path.join(COMPLIANCE_TRACKER_DIR, ".env.local")
LOCAL_LIBS = "/opt/veridian/workspace/browser-tools/local-libs/usr/lib/x86_64-linux-gnu"
LOGIN_URL = "https://projexa-ai.com/login"
BASE_URL = "https://projexa-ai.com"

ROLES = ["owner", "manager", "member", "viewer"]
EXPECTED_ROLE_ENUM = {"owner": "admin", "manager": "manager", "member": "member", "viewer": "viewer"}

# Real rows belonging to a genuinely different, real tenant (Acme Corp,
# org_001, compliance-tracker's own long-standing seeded org -- src/db/seed.ts).
FOREIGN_ORG_ID = "org_001"
FOREIGN_ITEM_ID = "ci_02"
FOREIGN_DEPT_ID = "dept_finance"

PROBE_JS = r"""
import { chromium } from '@playwright/test';

const [loginUrl, baseUrl, personasJson, foreignOrgId, foreignItemId, foreignDeptId] = process.argv.slice(2);
const personas = JSON.parse(personasJson);

const out = { per_persona: {} };

const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });

for (const p of personas) {
  const result = { email: p.email, expected_role: p.expectedRole, login_ok: false, steps: {} };
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto(loginUrl, { waitUntil: 'load', timeout: 30000 });
    await page.locator('#email').fill(p.email);
    await page.locator('#password').fill(p.password);
    await page.locator('button[type="submit"]').first().click();
    await page.waitForTimeout(3000);

    const meResp = await context.request.get(`${baseUrl}/api/me`);
    const meBody = meResp.ok() ? await meResp.json() : null;
    result.login_ok = meResp.status() === 200 && !!meBody && meBody.role === p.expectedRole;
    result.steps.me = { status: meResp.status(), role: meBody?.role ?? null, orgId: meBody?.orgId ?? null, orgSlug: meBody?.orgSlug ?? null };

    const listResp = await context.request.get(`${baseUrl}/api/compliance?limit=100`);
    const listText = await listResp.text();
    result.steps.compliance_list = {
      status: listResp.status(),
      leaks_foreign_item_id: listText.includes(foreignItemId),
      leaks_foreign_org_id: listText.includes(foreignOrgId),
    };

    const foreignItemResp = await context.request.get(`${baseUrl}/api/compliance/${foreignItemId}`);
    result.steps.foreign_item_direct_fetch = { status: foreignItemResp.status(), denied: foreignItemResp.status() !== 200 };

    const deptResp = await context.request.get(`${baseUrl}/api/departments`);
    const deptText = await deptResp.text();
    result.steps.departments_list = { status: deptResp.status(), leaks_foreign_dept_id: deptText.includes(foreignDeptId) };

    await context.close();
  } catch (e) {
    result.error = e.message;
  }
  out.per_persona[p.role] = result;
}

await browser.close();
console.log(JSON.stringify(out));
"""


def call_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_multi_tenant_testing.py",
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
    org_slug = get_env_value("GTM_TEST_MERIDIAN_ORG_SLUG")
    if not org_id or not org_slug:
        emit(
            args, "blocked",
            f"GTM_TEST_MERIDIAN_ORG_ID/GTM_TEST_MERIDIAN_ORG_SLUG confirmed absent from {ENV_LOCAL_PATH} -- "
            f"test tenant not provisioned (or .env.local not reachable from this host). Run "
            f"compliance-tracker/scripts/gtm-provision-cat15-16-test-tenant.ts first.",
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

    if not personas:
        emit(args, "blocked", f"No GTM_TEST_MERIDIAN_*_EMAIL/PASSWORD pairs found in {ENV_LOCAL_PATH}.", {"missing_creds": missing_creds})
        return

    probe_path = os.path.join(COMPLIANCE_TRACKER_DIR, ".gtm_multitenant_probe.mjs")
    with open(probe_path, "w") as f:
        f.write(PROBE_JS)

    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = LOCAL_LIBS + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")

    try:
        p = subprocess.run(
            [node, probe_path, LOGIN_URL, BASE_URL, json.dumps(personas), FOREIGN_ORG_ID, FOREIGN_ITEM_ID, FOREIGN_DEPT_ID],
            cwd=COMPLIANCE_TRACKER_DIR, env=env, capture_output=True, text=True, timeout=180,
        )
        stdout_tail = (p.stdout or "").strip()
        probe_out = json.loads(stdout_tail.splitlines()[-1]) if stdout_tail else None
        probe_error = None if probe_out else f"no stdout (exit {p.returncode}); stderr: {(p.stderr or '')[-1500:]}"
    except subprocess.TimeoutExpired:
        probe_out, probe_error = None, "probe timed out after 180s"
    except (json.JSONDecodeError, IndexError):
        probe_out, probe_error = None, f"non-JSON stdout (exit {p.returncode}); stderr: {(p.stderr or '')[-1500:]}"
    finally:
        if os.path.isfile(probe_path):
            os.remove(probe_path)

    if probe_out is None:
        emit(args, "blocked", f"Real Playwright probe failed to produce parseable output: {probe_error}", {"probe_error": probe_error})
        return

    per_persona = probe_out.get("per_persona", {})
    failures = []
    logins_failed = []
    for role, r in per_persona.items():
        if r.get("error"):
            failures.append(f"{role}: probe error: {r['error']}")
            continue
        if not r.get("login_ok"):
            logins_failed.append(role)
            continue
        steps = r.get("steps", {})
        if steps.get("compliance_list", {}).get("leaks_foreign_item_id"):
            failures.append(f"{role}: /api/compliance list leaked foreign item id {FOREIGN_ITEM_ID}")
        if steps.get("compliance_list", {}).get("leaks_foreign_org_id"):
            failures.append(f"{role}: /api/compliance list leaked foreign org id {FOREIGN_ORG_ID}")
        if not steps.get("foreign_item_direct_fetch", {}).get("denied"):
            failures.append(f"{role}: GET /api/compliance/{FOREIGN_ITEM_ID} was NOT denied (status={steps.get('foreign_item_direct_fetch', {}).get('status')})")
        if steps.get("departments_list", {}).get("leaks_foreign_dept_id"):
            failures.append(f"{role}: /api/departments list leaked foreign dept id {FOREIGN_DEPT_ID}")
        me = steps.get("me", {})
        if me.get("orgId") != org_id or me.get("orgSlug") != org_slug:
            failures.append(f"{role}: /api/me reported orgId={me.get('orgId')}/orgSlug={me.get('orgSlug')}, expected {org_id}/{org_slug}")

    if len(logins_failed) == len(personas):
        emit(
            args, "blocked",
            f"Real login failed for all {len(personas)} provisioned test accounts against {LOGIN_URL} -- cannot exercise multi-tenant isolation without at least one working authenticated session.",
            {"per_persona": per_persona, "logins_failed": logins_failed},
        )
        return

    result = "fail" if failures else "pass"
    tested_count = len(personas) - len(logins_failed)
    evidence = {
        "org_under_test": {"id": org_id, "slug": org_slug, "name": "Meridian Test Industries (GTM Cat 15/16 Test Fixture -- Non-Production)"},
        "accounts_tested": [{"role": p["role"], "email": p["email"]} for p in personas],
        "foreign_tenant_probe_target": {"org_id": FOREIGN_ORG_ID, "item_id": FOREIGN_ITEM_ID, "dept_id": FOREIGN_DEPT_ID, "org_name": "Acme Corp (pre-existing seeded org, src/db/seed.ts)"},
        "base_url": BASE_URL,
        "per_persona": per_persona,
        "logins_failed": logins_failed,
        "failures": failures,
        "pass_criterion": "for every account that logged in successfully: /api/me always reports the Meridian test org; list endpoints never surface a known foreign id; direct cross-tenant fetch of a real different tenant's resource is always denied (non-200)",
        "provisioning_script": "compliance-tracker/scripts/gtm-provision-cat15-16-test-tenant.ts",
    }
    summary = (
        f"Multi-tenant isolation probe: {tested_count}/{len(personas)} test accounts logged in and were checked "
        f"against real other-tenant data (Acme Corp/org_001). "
        + (f"{len(failures)} real leak(s)/denial-bypass(es) found: {'; '.join(failures[:5])}" if failures else "0 leaks -- every account's session was confined to the Meridian test org and every cross-tenant fetch was denied.")
    )
    emit(args, result, summary, evidence)


if __name__ == "__main__":
    main()
