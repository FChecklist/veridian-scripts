#!/usr/bin/env python3
"""gtm_check_ux_audit.py -- real, re-runnable check for GTM certification
category_index=23 ("UX audit"), evaluated against Jakob Nielsen's real 10
usability heuristics (the industry-standard deterministic heuristic-
evaluation framework; heuristic wording here follows eleken.co's published
checklist based on Nielsen's original 10). Severity is scored 0-4 per
Maze's heuristic-evaluation severity scale (0=not a problem .. 4=usability
catastrophe) -- see https://maze.co for that methodology; Maze also notes
3 evaluators typically catch ~60% of real usability issues in a manual
heuristic review. That 60%-with-3-evaluators figure is cited here only as
METHODOLOGY CONTEXT for why heuristic evaluation is inherently a sampling
exercise, not a target this single automated+AI-assisted pass is claiming
to satisfy with 3 independent human evaluators -- it isn't; be honest about
that gap every time this script's results are read.

WHAT IS GENUINELY DETERMINISTIC vs. AI-ASSISTED-WITH-A-FIXED-RUBRIC
(documented honestly, not overclaimed):

  DETERMINISTIC (same every run, no model call involved):
    - Node/Playwright/local-libs presence checks (same reused user-space
      library set as gtm_check_accessibility_testing.py /
      gtm_check_responsive_testing.py -- LD_LIBRARY_PATH pointed at
      /opt/veridian/workspace/browser-tools/local-libs, not a new install).
    - Real Playwright navigation to each real, live, public, PRE-AUTH page
      (see PAGES below) plus one deliberately-nonexistent path (404
      handling probe). Real HTTP status, real final URL after redirects,
      real load time, real console/page JS errors -- captured, not
      narrated.
    - Real DOM extraction per page: headings, nav/footer links, buttons,
      form field attributes (required/pattern/autocomplete/labels),
      aria-live/role=status/role=alert counts, loading/spinner/skeleton
      class-name matches, skip-link presence, positive-tabindex count,
      image alt-text coverage, DOM node count, visible text length.
      This extraction logic is fixed and produces the same structured
      JSON for the same live page content every run.
    - The PASS/FAIL/BLOCKED aggregation rule itself (aggregate_heuristic_
      outputs()): zero severity-3-or-4 findings across all 10 heuristics
      = pass; any real severity-3-or-4 finding = fail; any heuristic whose
      AI-assisted pass could not genuinely run = blocked (never silently
      scored as if it had).

  AI-ASSISTED WITH A FIXED RUBRIC (genuine judgment is unavoidable here --
  this is NOT deterministic in the strict sense, and this script does not
  claim it is):
    - Turning the deterministic evidence above into a 0-4 severity score
      and specific findings PER heuristic requires judgment (e.g. deciding
      whether inconsistent button copy across two real pages rises to
      "major" or is merely "cosmetic"). This script makes that judgment
      call via the same real `claude -p` Claude Code CLI subscription
      dispatch mechanism already used elsewhere on this server for bounded
      judgment calls (credit-accountant.py's claude_judgment_call() is the
      direct precedent this reuses get_claude_oauth_env() from) --
      CLAUDE_CODE_OAUTH_TOKEN subscription auth, NEVER the metered
      OpenRouter/API-key pool (ANTHROPIC_API_KEY is disabled server-wide;
      no ANTHROPIC_BASE_URL override is set).
    - Determinism-as-honestly-as-possible: the SAME fixed prompt template
      (build_prompt()) is filled with the SAME structured deterministic
      evidence and the SAME severity-scale definition every run, and the
      model is instructed to score ONLY from the evidence given, never to
      invent findings. This bounds variance but does NOT eliminate it --
      an LLM judgment call is not bit-for-bit reproducible the way the
      deterministic extraction above is. Re-running this script against an
      unchanged live site can, in principle, produce a different severity
      on a borderline finding. What IS reproducible: the evidence
      collection, the prompt, the rubric, and the pass/fail arithmetic.

ABSOLUTE STANDING RULE HONORED, WITH EXTRA MARGIN: never enter a real
password into any login/signup field. This script goes further than the
letter of that rule -- it never types into ANY form field on ANY page
(not email, not password), because some real signup/login forms fire a
real backend call on blur (e.g. an email-exists check), and this script's
job is to OBSERVE the live pre-auth surfaces, not to interact with their
backend. All evidence is collected by passive navigation + DOM inspection
only. Categories 15/16 (multi-tenant / role-permission testing) are the
correct place for anything requiring an authenticated session; this
script never attempts one.

PAGES evaluated (pre-auth, reachable without any credential):
  /login, /signup, /pricing, /contact, /help (the last is expected, and
  reconfirmed live every run rather than hardcoded, to redirect to /login
  -- itself real evidence for heuristic 10). Plus one deliberately bogus
  path for a real 404/error-page probe (heuristic 9 evidence). "/" is not
  probed separately: it live-redirects to /login (reconfirmed at write
  time), so it has no distinct pre-auth content of its own.

Pass bar (documented, fixed, not adjustable at call time):
  PASS <=> all 10 heuristics were genuinely AI-evaluated against real
           evidence AND zero real findings scored severity 3 or 4.
  FAIL <=> all 10 heuristics were genuinely evaluated AND at least one
           real finding scored severity 3 or 4 (a genuine fail, not
           "blocked").
  BLOCKED <=> node/Playwright/local-libs absent, the live probe could not
           genuinely run, or the `claude -p` judgment call for ANY of the
           10 heuristics did not genuinely produce a parseable severity
           (tool absent, timeout, malformed response) -- never fabricate
           a pass/fail for a heuristic that didn't genuinely get scored.

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=23's result.

Usage:
  gtm_check_ux_audit.py [--base-url URL] [--pages /login,/signup,...]
                         [--max-budget-usd 0.30] [--skip-ai-eval]
  --skip-ai-eval runs only the deterministic evidence-collection pass and
  reports category_index=23 as "blocked" (AI pass intentionally skipped) --
  it exists purely as a fast, no-spend way to exercise/debug the
  deterministic half; it must never be used to manufacture a pass.
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
CATEGORY_INDEX = 23
SCRIPT_NAME = "gtm_check_ux_audit.py"

DEFAULT_BASE_URL = "https://projexa-ai.com"
DEFAULT_PAGES = ["/login", "/signup", "/pricing", "/contact", "/help"]
ERROR_PAGE_PROBE_PATH = "/__gtm_ux_audit_nonexistent_page_probe__"

COMPLIANCE_TRACKER_DIR = "/opt/veridian/repos/compliance-tracker"
LOCAL_LIBS = "/opt/veridian/workspace/browser-tools/local-libs/usr/lib/x86_64-linux-gnu"

# Reused, not duplicated: credit-accountant.py is the established, tested
# source of "how does a script outside systemd get CLAUDE_CODE_OAUTH_TOKEN"
# on this server -- same reuse pattern gtm_write_category_result.py already
# uses for superboss-register.py's _connect()/_write_lock().
CREDIT_ACCOUNTANT_PATH = "/opt/veridian/scripts/credit-accountant.py"

CLAUDE_MODEL = "sonnet"
CLAUDE_EFFORT = "high"
CLAUDE_MAX_BUDGET_USD_DEFAULT = 0.30
CLAUDE_JUDGMENT_TIMEOUT_S = 120

FAIL_SEVERITIES = (3, 4)
SEVERITY_LABELS = {
    0: "not a usability problem",
    1: "cosmetic problem -- fix if extra time",
    2: "minor usability problem -- low priority",
    3: "major usability problem -- high priority",
    4: "usability catastrophe -- imperative to fix",
}

# Nielsen's 10 heuristics, wording per eleken.co's published checklist
# (itself based on Nielsen's original 10 usability heuristics for
# interface design). Fixed text -- do not rephrase between runs.
HEURISTICS = [
    {
        "id": 1,
        "name": "Visibility of system status",
        "definition": (
            "The design should always keep users informed about what is "
            "going on, through appropriate feedback within a reasonable "
            "amount of time (loading states, progress indicators, live "
            "regions, confirmation of actions taken)."
        ),
        "evidence_keys": ["liveRegionCount", "loadingIndicatorCount", "consoleErrors", "pageErrors", "loadTimeMs", "httpStatus"],
    },
    {
        "id": 2,
        "name": "Match between the system and the real world",
        "definition": (
            "The design should speak the users' language, using words, "
            "phrases, and concepts familiar to the user rather than "
            "internal jargon, and follow real-world conventions so "
            "information appears in a natural, logical order."
        ),
        "evidence_keys": ["title", "metaDescription", "headings", "navLinks", "buttons"],
    },
    {
        "id": 3,
        "name": "User control and freedom",
        "definition": (
            "Users often perform actions by mistake. They need a clearly "
            "marked 'emergency exit' (cancel, back, undo, a way home) to "
            "leave an unwanted state without an extended process."
        ),
        "evidence_keys": ["navLinks", "footerLinks", "buttons"],
    },
    {
        "id": 4,
        "name": "Consistency and standards",
        "definition": (
            "Users should not have to wonder whether different words, "
            "situations, or actions mean the same thing. Follow platform "
            "and internal conventions consistently across pages."
        ),
        "evidence_keys": ["navLinks", "footerLinks", "headings", "forms", "buttons"],
    },
    {
        "id": 5,
        "name": "Error prevention",
        "definition": (
            "Good error messages matter, but the best designs prevent "
            "problems before they happen -- eliminating error-prone "
            "conditions or checking for them via constraints like "
            "required/pattern/type attributes and confirmations."
        ),
        "evidence_keys": ["forms"],
    },
    {
        "id": 6,
        "name": "Recognition rather than recall",
        "definition": (
            "Minimize the user's memory load by making elements, labels, "
            "and options visible (real <label>s, placeholders, "
            "autocomplete) rather than requiring users to remember "
            "information from elsewhere in the interface."
        ),
        "evidence_keys": ["forms", "buttons", "navLinks"],
    },
    {
        "id": 7,
        "name": "Flexibility and efficiency of use",
        "definition": (
            "The design should cater to both novice and expert users -- "
            "accelerators/shortcuts hidden from novices, keyboard "
            "navigability, and autocomplete/autofill support for "
            "efficient repeat use."
        ),
        "evidence_keys": ["hasSkipLink", "positiveTabindexCount", "mentionsKeyboardShortcuts", "forms"],
    },
    {
        "id": 8,
        "name": "Aesthetic and minimalist design",
        "definition": (
            "Interfaces should not contain irrelevant or rarely needed "
            "information; every extra unit of information competes with "
            "relevant units and diminishes their visibility."
        ),
        "evidence_keys": ["domNodeCount", "visibleTextLength", "imageCount", "headings", "buttons", "navLinks"],
    },
    {
        "id": 9,
        "name": "Help users recognize, diagnose, and recover from errors",
        "definition": (
            "Error messages should be in plain language (no raw codes), "
            "precisely state the problem, and constructively suggest a "
            "solution -- including a real error/404 page giving the user "
            "a way forward."
        ),
        "evidence_keys": [],  # custom evidence: errorPageProbe + liveRegionCount per page
    },
    {
        "id": 10,
        "name": "Help and documentation",
        "definition": (
            "It's best if the system needs no extra explanation, but "
            "where needed, help/documentation should be easy to search, "
            "focused on the user's task, and reachable when needed."
        ),
        "evidence_keys": ["helpLinks", "footerLinks", "redirected", "finalUrl"],
    },
]

PROBE_JS = r"""
import { chromium } from '@playwright/test';

const baseUrl = process.argv[2];
const pages = process.argv[3].split(',').filter(Boolean);
const errorPath = process.argv[4];

async function extractPage(page) {
  return page.evaluate(() => {
    const q = (sel) => Array.from(document.querySelectorAll(sel));
    // OCID-020 category 23 fix (UMR-20260806-132527-30dc): plain el.textContent
    // concatenated a purely decorative icon-letter glyph (a single-character
    // logo badge marked aria-hidden) together with the adjacent real brand
    // text inside the same <a>, producing a nonsense string (e.g. a "V" badge
    // + "VERIDIAN AI" text read back as "VVERIDIAN AI") that no real user
    // ever sees and no screen reader ever announces -- aria-hidden content is
    // by definition excluded from the accessible name/text a user encounters,
    // so it must also be excluded here. Strip aria-hidden="true" descendants
    // (on a detached clone, so the live DOM is never touched) before reading
    // textContent. Real, confirmed false positive this closes: compliance-
    // tracker's /pricing nav renders a decorative single-letter "V" logo
    // badge immediately before its "VERIDIAN AI" brand text inside the same
    // <Link>; before this fix, navLinks reported that link's text as
    // "VVERIDIAN AI" (a typo that does not exist on the rendered page) and
    // this contributed to a real heuristic-4 (consistency) severity-3
    // finding. See compliance-tracker PR #987 for the corresponding product
    // fix (aria-hidden added to the decorative badge itself).
    const txt = (el) => {
      const clone = el.cloneNode(true);
      clone.querySelectorAll('[aria-hidden="true"]').forEach((n) => n.remove());
      return (clone.textContent || '').trim().slice(0, 120);
    };
    const headings = q('h1,h2,h3,h4,h5,h6').map((el) => ({ tag: el.tagName.toLowerCase(), text: txt(el) }));
    const navLinks = q('nav a, header a').map((el) => ({ text: txt(el), href: el.getAttribute('href') }));
    const footerLinks = q('footer a').map((el) => ({ text: txt(el), href: el.getAttribute('href') }));
    const helpLinks = q('a').filter((el) => /help|docs|documentation|faq|support/i.test(el.textContent || '') || /help|docs|faq|support/i.test(el.getAttribute('href') || '')).map((el) => ({ text: txt(el), href: el.getAttribute('href') }));
    const forms = q('form').map((f) => ({
      action: f.getAttribute('action'),
      hasNovalidate: f.hasAttribute('novalidate'),
      fields: Array.from(f.querySelectorAll('input,select,textarea')).map((inp) => {
        const id = inp.id;
        const hasLabelFor = id ? !!document.querySelector(`label[for="${id}"]`) : false;
        const wrappedInLabel = !!inp.closest('label');
        return {
          type: inp.getAttribute('type') || inp.tagName.toLowerCase(),
          name: inp.getAttribute('name'),
          required: inp.hasAttribute('required'),
          pattern: inp.getAttribute('pattern'),
          autocomplete: inp.getAttribute('autocomplete'),
          placeholder: inp.getAttribute('placeholder'),
          hasLabel: hasLabelFor || wrappedInLabel || !!inp.getAttribute('aria-label') || !!inp.getAttribute('aria-labelledby'),
        };
      }),
    }));
    const buttons = q('button, a[role="button"], input[type="submit"]').map((el) => ({ text: txt(el) || (el.value || '').trim().slice(0, 60), disabled: !!el.disabled }));
    const liveRegionCount = q('[role="status"],[role="alert"],[aria-live]').length;
    const loadingIndicatorCount = q('[class*="spinner" i],[class*="loading" i],[class*="skeleton" i]').length;
    const hasSkipLink = q('a[href^="#"]').some((el) => /skip/i.test(el.textContent || ''));
    const images = q('img');
    const imagesMissingAlt = images.filter((img) => !img.hasAttribute('alt')).length;
    const positiveTabindexCount = q('[tabindex]').filter((el) => parseInt(el.getAttribute('tabindex'), 10) > 0).length;
    const bodyText = document.body ? document.body.innerText || '' : '';
    return {
      title: document.title,
      metaDescription: (document.querySelector('meta[name="description"]') || {}).content || null,
      h1Count: headings.filter((h) => h.tag === 'h1').length,
      headings, navLinks, footerLinks, helpLinks, forms, buttons,
      liveRegionCount, loadingIndicatorCount, hasSkipLink,
      imageCount: images.length, imagesMissingAlt, positiveTabindexCount,
      domNodeCount: document.querySelectorAll('*').length,
      visibleTextLength: bodyText.length,
      mentionsKeyboardShortcuts: /keyboard shortcut/i.test(bodyText),
    };
  });
}

async function probeOne(browser, path) {
  const page = await browser.newPage();
  const consoleErrors = [];
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message));
  page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });
  const url = baseUrl + path;
  const out = { path, requestedUrl: url };
  try {
    const t0 = Date.now();
    const resp = await page.goto(url, { waitUntil: 'load', timeout: 30000 });
    const t1 = Date.now();
    out.httpStatus = resp ? resp.status() : null;
    out.loadOk = !!resp && resp.status() >= 200 && resp.status() < 400;
    out.loadTimeMs = t1 - t0;
    out.finalUrl = page.url();
    out.redirected = (() => { try { return new URL(out.finalUrl).pathname !== path; } catch { return false; } })();
    out.consoleErrors = consoleErrors;
    out.pageErrors = pageErrors;
    if (resp) {
      // Extract DOM evidence whenever a response actually rendered a page --
      // including real 4xx/5xx error pages (e.g. a Next.js custom 404), which
      // is exactly the content heuristic 9 (error recovery) needs to judge.
      // loadOk (2xx-3xx only) stays separate/informational.
      Object.assign(out, await extractPage(page));
    }
  } catch (e) {
    out.error = e.message;
    out.loadOk = false;
  } finally {
    await page.close();
  }
  return out;
}

const out = { generatedAt: new Date().toISOString(), pages: [], errorPageProbe: null, error: null };
try {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  for (const p of pages) {
    out.pages.push(await probeOne(browser, p));
  }
  out.errorPageProbe = await probeOne(browser, errorPath);
  await browser.close();
} catch (e) {
  out.error = e.message;
}
console.log(JSON.stringify(out));
"""


def load_credit_accountant():
    spec = importlib.util.spec_from_file_location("credit_accountant", CREDIT_ACCOUNTANT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def call_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", SCRIPT_NAME,
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    print("Calling writer:", " ".join(cmd), file=sys.stderr)
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        sys.exit(p.returncode)


def run_probe(base_url, pages, timeout_s=120):
    """Runs the Playwright probe; returns (result_obj_or_None, error_str_or_None)."""
    probe_path = os.path.join(COMPLIANCE_TRACKER_DIR, ".gtm_ux_audit_probe.mjs")
    with open(probe_path, "w") as f:
        f.write(PROBE_JS)
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = LOCAL_LIBS + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    try:
        p = subprocess.run(
            ["node", probe_path, base_url, ",".join(pages), ERROR_PAGE_PROBE_PATH],
            cwd=COMPLIANCE_TRACKER_DIR, env=env,
            capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return None, f"Playwright probe against {base_url} timed out after {timeout_s}s."
    finally:
        if os.path.isfile(probe_path):
            os.remove(probe_path)

    stdout_tail = (p.stdout or "").strip()
    try:
        result_obj = json.loads(stdout_tail.splitlines()[-1]) if stdout_tail else None
    except (json.JSONDecodeError, IndexError):
        result_obj = None
    if result_obj is None:
        return None, (
            f"Playwright probe process exit {p.returncode} produced no parseable JSON. "
            f"stderr_tail={(p.stderr or '')[-1000:]}"
        )
    return result_obj, None


def build_heuristic_evidence(heuristic, probe_result):
    """Curated, per-heuristic subset of the deterministic evidence -- pure
    function, no I/O, testable in isolation."""
    hid = heuristic["id"]
    pages = probe_result.get("pages", [])

    def trim(page, keys):
        base = {k: page.get(k) for k in ("path", "httpStatus", "loadOk", "finalUrl", "redirected")}
        for k in keys:
            base[k] = page.get(k)
        return base

    if hid == 9:
        return {
            "error_page_probe": probe_result.get("errorPageProbe"),
            "pages_live_region_counts": [
                {"path": p.get("path"), "liveRegionCount": p.get("liveRegionCount")} for p in pages
            ],
        }

    keys = heuristic["evidence_keys"]
    return {"pages": [trim(p, keys) for p in pages]}


def build_prompt(heuristic, evidence):
    severity_scale = "\n".join(f"  {k} = {v}" for k, v in SEVERITY_LABELS.items())
    return f"""You are conducting a Nielsen heuristic-evaluation usability audit of the \
live, pre-authentication surfaces of https://projexa-ai.com.

Heuristic {heuristic['id']}/10: {heuristic['name']}
Definition (Jakob Nielsen's 10 Usability Heuristics, wording per eleken.co's \
published checklist): {heuristic['definition']}

Severity scale (Maze heuristic-evaluation methodology, 0-4):
{severity_scale}

You are given REAL, automatically-collected evidence from live pages (no \
login was performed; no credentials were entered anywhere -- these are all \
pre-auth pages observed passively). Base your evaluation STRICTLY on this \
evidence. Do not assume the presence of features not shown. If the evidence \
is genuinely insufficient to identify a real problem for THIS heuristic, \
score severity 0 and say so -- do not invent a finding to seem thorough.

EVIDENCE (JSON):
{json.dumps(evidence, indent=2)[:8000]}

Respond with ONLY a single JSON object, no markdown code fences, no other \
text, in exactly this shape:
{{"severity": <int 0-4, the single worst real finding's severity for this \
heuristic>, "findings": [{{"description": "<specific, evidence-grounded \
issue>", "severity": <int 0-4>, "page": "<path this finding relates to, or \
'all'>"}}], "rationale": "<1-2 sentences citing the specific evidence that \
drove the top severity score>"}}
If there are no real findings, return an empty findings array and severity 0."""


def parse_claude_json_response(raw_text):
    """Strips optional markdown fences and parses the model's JSON answer.
    Uses json.JSONDecoder.raw_decode() rather than json.loads() so a real
    response that is a well-formed JSON object FOLLOWED by extra trailing
    text (observed in practice -- the model sometimes appends a trailing
    remark after the object despite being told not to) still parses on the
    object itself instead of failing the whole heuristic as 'blocked' over
    ignorable trailing content. Leading junk before the object is NOT
    tolerated -- only trailing. Pure function -- testable without a
    network/subprocess call."""
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    obj, _end_index = json.JSONDecoder().raw_decode(text)
    if "severity" not in obj or not isinstance(obj["severity"], int) or not (0 <= obj["severity"] <= 4):
        raise ValueError(f"missing/invalid 'severity' field in model response: {obj}")
    obj.setdefault("findings", [])
    obj.setdefault("rationale", "")
    return obj


def call_claude_judgment(prompt, env, max_budget_usd, timeout_s):
    """Real `claude -p` dispatch -- subscription-authenticated
    (CLAUDE_CODE_OAUTH_TOKEN), never the metered OpenRouter/API-key pool.
    Returns (parsed_obj_or_None, error_str_or_None)."""
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", CLAUDE_MODEL, "--effort", CLAUDE_EFFORT,
             "--output-format", "json", "--max-budget-usd", str(max_budget_usd)],
            capture_output=True, text=True, timeout=timeout_s, env=env,
        )
    except subprocess.TimeoutExpired:
        return None, "claude -p judgment call timed out"
    except Exception as e:
        return None, f"claude -p judgment call failed to launch: {e}"

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, f"claude -p produced no parseable outer JSON (exit {result.returncode}); stderr_tail={(result.stderr or '')[-500:]}"
    if data.get("is_error"):
        return None, f"claude -p reported is_error: {str(data.get('result', ''))[:300]}"
    raw_text = (data.get("result") or "").strip()
    try:
        return parse_claude_json_response(raw_text), None
    except (json.JSONDecodeError, ValueError) as e:
        return None, f"unparseable heuristic judgment response: {e}; raw={raw_text[:300]}"


def aggregate_heuristic_outputs(outputs):
    """outputs: dict[int -> {"status": "ok"|"blocked", "severity": int|None,
    "findings": list, "error": str|None}]
    Returns (overall_result, max_severity_or_None, fail_heuristic_ids, blocked_heuristic_ids).
    Pure function -- this IS the deterministic pass/fail arithmetic."""
    blocked_ids = sorted(hid for hid, o in outputs.items() if o.get("status") != "ok")
    if blocked_ids:
        return "blocked", None, [], blocked_ids
    max_sev = max((o["severity"] for o in outputs.values()), default=0)
    fail_ids = sorted(hid for hid, o in outputs.items() if o.get("severity") in FAIL_SEVERITIES)
    return ("fail" if fail_ids else "pass"), max_sev, fail_ids, blocked_ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--pages", default=",".join(DEFAULT_PAGES))
    ap.add_argument("--max-budget-usd", type=float, default=CLAUDE_MAX_BUDGET_USD_DEFAULT)
    ap.add_argument("--skip-ai-eval", action="store_true",
                     help="Deterministic evidence collection only; reports 'blocked' honestly. Never produces a pass.")
    args = ap.parse_args()
    pages = [p.strip() for p in args.pages.split(",") if p.strip()]

    node = shutil.which("node")
    if not node:
        call_writer("blocked", "node confirmed absent from PATH; cannot run Playwright.", {"missing_tools": ["node"]})
        return
    pw_dir = os.path.join(COMPLIANCE_TRACKER_DIR, "node_modules", "@playwright", "test")
    if not os.path.isdir(pw_dir):
        call_writer("blocked", f"@playwright/test confirmed absent at {pw_dir}.", {"missing": "@playwright/test", "checked_path": pw_dir})
        return
    if not os.path.isdir(LOCAL_LIBS):
        call_writer("blocked", f"Real user-space shared-library set at {LOCAL_LIBS} confirmed absent; no working browser engine available.", {"local_libs_dir": LOCAL_LIBS})
        return
    if not shutil.which("claude") and not args.skip_ai_eval:
        call_writer("blocked", "claude CLI confirmed absent from PATH; cannot run the AI-assisted heuristic judgment pass.", {"missing_tools": ["claude"]})
        return

    probe_result, probe_error = run_probe(args.base_url, pages)
    if probe_result is None:
        call_writer("blocked", probe_error, {"base_url": args.base_url, "pages": pages})
        return
    if probe_result.get("error") and not probe_result.get("pages"):
        call_writer("blocked", f"Real browser/probe error before any page could be evaluated: {probe_result['error']}", {"probe_result": probe_result})
        return

    accountant = load_credit_accountant() if not args.skip_ai_eval else None
    env = accountant.get_claude_oauth_env() if accountant else None

    heuristic_outputs = {}
    for h in HEURISTICS:
        evidence = build_heuristic_evidence(h, probe_result)
        if args.skip_ai_eval:
            heuristic_outputs[h["id"]] = {"status": "blocked", "severity": None, "findings": [], "error": "AI evaluation pass intentionally skipped (--skip-ai-eval)", "evidence": evidence}
            continue
        prompt = build_prompt(h, evidence)
        parsed, err = call_claude_judgment(prompt, env, args.max_budget_usd, CLAUDE_JUDGMENT_TIMEOUT_S)
        if parsed is None:
            heuristic_outputs[h["id"]] = {"status": "blocked", "severity": None, "findings": [], "error": err, "evidence": evidence}
        else:
            heuristic_outputs[h["id"]] = {
                "status": "ok", "severity": parsed["severity"], "findings": parsed["findings"],
                "rationale": parsed.get("rationale", ""), "error": None, "evidence": evidence,
            }

    overall_result, max_sev, fail_ids, blocked_ids = aggregate_heuristic_outputs(heuristic_outputs)

    per_heuristic_summary = {
        str(h["id"]): {
            "name": h["name"],
            "status": heuristic_outputs[h["id"]]["status"],
            "severity": heuristic_outputs[h["id"]]["severity"],
            "findings": heuristic_outputs[h["id"]]["findings"],
            "rationale": heuristic_outputs[h["id"]].get("rationale", ""),
            "error": heuristic_outputs[h["id"]]["error"],
        }
        for h in HEURISTICS
    }
    evidence = {
        "base_url": args.base_url,
        "pages_checked": pages,
        "error_page_probe_path": ERROR_PAGE_PROBE_PATH,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "pass_criterion": "zero real findings at severity 3 or 4 across all 10 Nielsen heuristics (Maze 0-4 severity scale); any heuristic whose AI-assisted pass didn't genuinely run makes the whole result 'blocked', never a fabricated pass/fail.",
        "methodology_note": "AI-assisted judgment via real claude -p (Claude Code CLI subscription dispatch, never metered API); Maze notes ~3 human evaluators typically catch ~60% of real usability issues in manual heuristic review -- cited as context for this single automated+AI-assisted pass's inherent coverage limits, not a claim this pass matches 3 independent human evaluators.",
        "max_severity": max_sev,
        "failing_heuristic_ids": fail_ids,
        "blocked_heuristic_ids": blocked_ids,
        "heuristics": per_heuristic_summary,
        "raw_probe_pages": probe_result.get("pages"),
        "raw_error_page_probe": probe_result.get("errorPageProbe"),
    }

    if overall_result == "blocked":
        summary = f"UX audit blocked: heuristic(s) {blocked_ids} did not genuinely produce a scored result (see evidence_json for per-heuristic error)."
    elif overall_result == "fail":
        detail = "; ".join(f"H{i}={heuristic_outputs[i]['severity']}({SEVERITY_LABELS[heuristic_outputs[i]['severity']]})" for i in fail_ids)
        summary = f"UX audit FAIL: {len(fail_ids)} heuristic(s) at severity>=3 across {len(pages)} pre-auth page(s) on {args.base_url}: {detail}."
    else:
        summary = f"UX audit PASS: all 10 Nielsen heuristics evaluated against {len(pages)} pre-auth page(s) on {args.base_url}, zero real findings at severity 3 or 4 (max_severity={max_sev})."

    call_writer(overall_result, summary, evidence)


if __name__ == "__main__":
    main()
