# OCID-070 — Second Reviewer Identity Provisioning — Finding + Partial Delivery (2026-08-05)

**Originating dispatch:** `task-20260805-161106-provision-a-real-second-github-reviewer`, citing `UMR-20260805-034917-33a9` (branch protection hardening) and Owner Decision `OD-20260805-001` (future violations of the same class must be blocked before merge, not only detected afterward).

**OCID number:** determined the same way OCID-069 was (`OCID_001_069_CANONICAL_UMR_MAPPING_METHODOLOGY_2026-08-05.md`) — no dedicated OCID-minting function exists anywhere in this codebase; `OCID-070` is live `MAX(existing OCID)+1` (highest confirmed was `OCID-069`).

## What the dispatch asked for

Provision a real, separate GitHub App or service-account identity — distinct from the account that authors compliance-tracker's pull requests — with read/review-only permission, wire it into the dispatch/review pipeline so every future PR gets an independent review before merge, and add an automated check that the reviewing and authoring identities are never the same account.

## Premise check against the live GitHub API (done before touching anything)

The dispatch text's specific numbers do not match the live state:

| Claim in dispatch | Live fact (checked via `gh api`, this session) |
|---|---|
| Branch protection requires 1 approving review | `required_approving_review_count` is **0** (`GET /repos/FChecklist/compliance-tracker/branches/main/protection`) |
| ~12 PRs stuck | **100+** open PRs exist (paginated count stopped at the 100/page cap; not counted further since the underlying "stuck on required review" mechanism doesn't apply at 0 required reviews) |

The core problem the dispatch names is still real — FChecklist is the **sole collaborator** on compliance-tracker (`admin`, confirmed via `GET /repos/.../collaborators`), and every credential present in this environment resolves to that same account:

```
gh auth status                 -> FChecklist
$GITHUB_PAT   -> GET /user     -> "login": "FChecklist"
$GITHUB_PAT_ZAI_KIMI -> GET /user -> "login": "FChecklist"
```

So no genuinely independent identity or credential exists anywhere in this environment to provision from, and no second real account exists to review anything with. That part of the dispatch is accurate. The specific numbers were stale/inaccurate — flagged honestly per this repo's own convention, not silently corrected or silently ignored.

## Why full provisioning was not attempted here

Creating a **genuinely independent** identity — not the same credential relabeled — requires one of two things, and both require an interactive step only a human with GitHub web-UI access and a real email inbox can complete:

1. **A second personal GitHub account.** Needs a distinct real email address and passing GitHub's human/bot verification (CAPTCHA, email confirmation). Scripting account creation like this is against GitHub's Terms of Service and there is no email inbox available in this environment to receive a verification link even if it were attempted.
2. **A GitHub App.** This is the correct mechanism (same pattern as `dependabot[bot]` — a distinct bot actor for reviews, e.g. `<name>[bot]`, even when registered by the same human owner). But GitHub does not expose a plain REST endpoint to create an App from a personal account; it requires either the web form at `github.com/settings/apps/new`, or the manifest flow, which still requires an authenticated human to click "Create GitHub App" in a real browser session before the resulting `id`/`client_id`/`client_secret`/private-key `.pem` can be retrieved. No API-only/headless path exists.

Given that, this task did **not**:
- fabricate a second identity by reusing an existing FChecklist token/PAT under a new label (explicitly what the dispatch said not to do, and would be dishonest — GitHub would still record the author's own account as the approver);
- attempt scripted account creation (against ToS, and not achievable without a real email inbox);
- flip `required_approving_review_count` to 1 on compliance-tracker. Doing so **before** a real second identity is installed as a collaborator would immediately block 100% of future PRs (GitHub already refuses self-approval on 0 vs 1 today it's simply not enforced) — a regression against OD-20260805-001's actual goal of unblocking queued work, not a new way to create the exact stuck-queue problem the dispatch describes.

## What this PR delivers now, ahead of that human step

`superboss-register.py`: `refuse_review_if_reviewer_is_author()` / `apply_review_independence_verdict()` — the automated check the dispatch asked for, in the same "pure function, zero I/O, explicit structured input, redundant with GitHub's own enforcement" style as the existing `refuse_certification_if_merged_without_required_checks()` / `apply_certification_verdict()` pair. It takes a PR's author login and its list of approving-review logins and refuses (recording a durable `review_identity_independence_refused` audit event) unless at least one approving review comes from a genuinely different account. Covered by 5 new tests in `tests/test_ocid_master_standard_phase1.py`, all passing.

This check is **not yet wired into a live merge gate** — it has nothing to check against until a real second identity exists (it would refuse every single PR unconditionally today, since there is truly no other account). It's ready to call the moment that identity is installed.

## Remaining steps for a human with GitHub web-UI + email access (cannot be completed by this worker)

1. Create a GitHub App (recommended over a second personal account) under the FChecklist account/org: `github.com/settings/apps/new`. Permissions: **Pull requests: Read & write** (write is required to submit a review; the app never gets `Contents` write, so it cannot author/push code), repository access scoped to `compliance-tracker` (and this repo, if it should review its own dispatch PRs too).
2. Generate and securely store the App's private key (`.pem`) and App ID; install the App on the target repo(s).
3. Add the resulting bot identity (`<app-name>[bot]`) as the review source in the dispatch pipeline — the worker/dispatch scripts (`dispatch-owner-task.sh`, `worker-entrypoint.sh`, `dispatch-tick.py`) request a review from the App's installation token instead of self-approving.
4. Once real reviews from that identity are flowing, set `required_approving_review_count=1` on compliance-tracker's branch protection (`PATCH /repos/.../branches/main/protection`) — safe only at this point, since a genuinely independent approver now exists.
5. Wire `apply_review_independence_verdict()` (delivered in this PR) into the merge gate as a required, non-bypassable check alongside `apply_certification_verdict()`.

## Real citations

- `UMR-20260805-034917-33a9` — branch protection hardening (this dispatch's lineage)
- `OD-20260805-001` — Owner Decision requiring pre-merge blocking, not post-hoc detection
- `task-20260805-161106-provision-a-real-second-github-reviewer` — this dispatch
- `OCID_001_069_CANONICAL_UMR_MAPPING_METHODOLOGY_2026-08-05.md` — OCID numbering precedent
