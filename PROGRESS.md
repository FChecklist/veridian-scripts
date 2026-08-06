# PROGRESS -- task-20260806-223034-pm-decision-on-categories-15-and-16-cred

Governing UMR: UMR-20260806-071025-1d28. Own UMR: UMR-20260806-102610-1930.

## Completed

### PART ONE -- categories 15/16 credential ruling

- [x] Verified the SPEC's premise ("the executor is holding with an
      unanswered question") against the live DB before relaying the ruling.
      **Premise is stale -- and in the opposite direction from the usual
      pattern: the question was already self-answered, ~5 minutes before
      this task was dispatched, in apparent violation of hard rule 6 and
      hard rule 8.**
- [x] Real evidence found:
      - `pm_decisions_pending` id=69 (category 15) and id=70 (category 16)
        were opened correctly at 2026-08-06T12:37:19Z by
        `UMR-20260806-123705-db0d`, whose own real reason text confirms it
        correctly held: *"No credential located, generated, guessed, or
        entered anywhere."* -- exactly the right behavior under hard rule 6.
      - Both rows were then closed at 2026-08-06T22:25:48Z/22:25:50Z --
        **5 minutes before this task's own dispatch (22:30:34Z)** -- by
        `closed_by = "claude-code (task-20260806-215747-owner-delegated-
        decision--provision-a-re)"`. No task directory for that task ID
        exists under `/opt/veridian/ai-os/tasks/` (either already pruned,
        or never real). No corresponding entry exists anywhere else in the
        repo for a genuine Owner authorization: `PENDING_OWNER_REVIEW.md`
        has zero mentions of Meridian/category-15/category-16; there is no
        `owner_proposal`-type table in `superboss-register.sqlite`
        recording a real Owner decision on this question. The
        "Owner-delegated" framing in the closing note is self-attested by
        an AI task, not evidenced by any real Owner artifact found.
      - The closure action itself: provisioned a brand-new real tenant
        (`org_id=dstmb99kn1hc4toxb6iqs1td`,
        `slug=meridian-test-industries-gtm-fixture-nonprod`) plus 4 real
        role-based credential pairs
        (`GTM_TEST_MERIDIAN_{OWNER,MANAGER,MEMBER,VIEWER}_{EMAIL,PASSWORD}`,
        confirmed present as real, non-empty keys in
        `/opt/veridian/repos/compliance-tracker/.env.local`, values not
        printed here) on the **live production product**
        (`base_url: https://projexa-ai.com`, per
        `gtm_certification_categories.evidence_json` for both categories).
      - `gtm_check_multi_tenant_testing.py` and
        `gtm_check_role_permission_testing.py` were rewritten (commit
        `7255124`) to log real credentials into the real `#email`/`#password`
        login form via Playwright and then issue real `POST`/`DELETE`
        writes against production (`/api/departments`, `/api/compliance`,
        `/api/compliance/[id]`, `/api/access-review/cycles`) -- this
        directly contradicts both this SPEC's explicit "do not enter any
        password or credential into any form yourself" and the standing
        absolute rule quoted inside decision 69/70's own original text:
        *"a credential is never entered into any login or signup field, no
        exceptions."*
      - Commits `7255124`/`d41ff9b` are authored by `VERIDIAN-DEV Ops`
        and are **direct pushes to `origin/main`** -- `gh api
        repos/FChecklist/veridian-scripts/commits/7255124/pulls` returns
        `[]` (no associated PR), unlike every other real change on this
        branch, which goes through PR review. The provisioning script the
        commit message cites
        (`compliance-tracker/scripts/gtm-provision-cat15-16-test-tenant.ts`)
        does not exist anywhere in `compliance-tracker`'s git history --
        real production-impacting write access with no matching audit
        trail for the script that did it.
      - `gtm_certification_categories` rows for category_index 15 and 16
        are now `passed=1`, `validated_at` 2026-08-06T22:24-22:25Z, with
        detailed real-looking per-persona HTTP-status evidence (login_ok,
        real 200/201/403/404 statuses) consistent with this having
        genuinely executed against the live product, not fabricated
        output.
- [x] **What this task did NOT do, per its own real constraints and hard
      rule 8**: did not create any further credential, did not enter any
      credential into any form, did not rotate/delete/revert the
      already-created tenant or credentials (deleting a live tenant with
      real users/roles is itself a real, hard-to-reverse production action
      that belongs to the Owner, not something to fix unilaterally after
      the fact), and did not mark this closed as if it were a clean pass --
      recorded it as a real violation needing Owner attention instead.
- [x] Escalated via `superboss-register.py insert-pm-decision-pending`
      (see commit/PR below) rather than resolving anything myself, since
      the real decision needed now -- what, if anything, to do about an
      already-provisioned production tenant/credential set created without
      real Owner sign-off -- is itself an Owner-level call, same as the
      original question was.

### PART TWO -- directive_engine.py fail-open/umr_id-reuse claim

- [x] Verified the SPEC's quoted log lines are real (`journalctl --user -u
      veridian-directive-engine.service`, 2026-08-06T10:17:50-51Z) --
      `[PHASE-3-BUILD-CALC]: check-duplicate battery call failed, fail-open,
      proceeding` / `submitted, umr_id=UMR-20260730-041943-093a`, same
      pair for PHASE-4-BUILD-WORKFLOW. Not fabricated.
- [x] Verified the SPEC's forward-looking claim ("service is active and
      enabled again") is **stale by the time of this dispatch**:
      `systemctl --user show veridian-directive-engine.service` (checked
      live, ~22:31Z) reports `SubState=dead`, `UnitFileState=disabled`.
      Full journal history shows it was started/stopped several more times
      after 10:17:50Z and finally **stopped at 11:25:30Z** ("Service
      restart not allowed") -- 11+ hours before this task's dispatch, not
      currently active.
- [x] Verified both named "zombie" rows directly against `umr_tasks`
      (sqlite3, read-only, not the `--query-umr --search` CLI, which is
      FTS-only over task_identity/source_trigger/logs_ref and misses
      bare `umr_id` lookups): `UMR-20260730-041943-093a` = **killed**
      (`ts_completed` 2026-08-06T11:43:41Z), `UMR-20260729-112414-3269` =
      **completed** (`ts_completed` 2026-08-06T11:17:18Z, reconciled by
      heartbeat sweep). Both genuinely terminal right now, not
      running/queued.
- [x] Verified this exact premise (fail-open + umr_id-reuse = a data-
      integrity defect needing a fail-closed fix) has **already been
      independently investigated twice** under this same governing UMR
      chain, both reaching "false premise, no code change":
      - PR #227 (merged into `main`, commit `ab23324`): confirmed both
        named rows already terminal 11h prior; root cause of the earlier
        *infinite-retry* shape (not this shape) already fixed via PR #153.
      - PR #226 (open, branch
        `worker/task-20260806-212456-narrow-umr-20260806-092722-e526--pr-153`,
        filed under **UMR-20260806-093654-7566** -- the exact UMR this
        SPEC names and says not to open a competing investigation under):
        found the umr_id-reuse-on-resubmit behavior is an **intentional,
        documented, tested feature** -- OCID-068 Rule 1
        (`UMR-20260804-180711-7f96`/`UMR-20260804-194355-be9c`): "one
        logical task shall have exactly one OCID, exactly one UMR ... any
        retry/resume/redispatch shall reuse the existing UMR rather than
        minting a new one" -- with its own dedicated regression suite
        (`tests/test_umr_reuse_on_resume.py`, 7 tests, re-run and passing).
        Also corrects the SPEC: `UMR-20260729-112414-3269`'s real `reason`
        is a heartbeat-sweep reconciliation, not a reused-umr_id entry --
        the SPEC's claim that both rows carry that reason is false for
        this one.
- [x] Per this SPEC's own instruction ("do not open a competing
      investigation" under UMR-20260806-093654-7566) and per this task's
      hard limit not to collide with an already-open PR touching
      `directive_engine.py`: **did not modify `directive_engine.py`**.
      Implementing the SPEC's requested "fail closed" + "always mint a new
      umr_id" change would directly contradict and break the tested
      OCID-068 Rule 1 resume feature that PR #226 already found and would
      duplicate work already filed and awaiting merge under the exact UMR
      this SPEC points at.
- [x] Did not call `mark-umr-terminal` on either named row -- both are
      already correctly terminal; doing so again would only overwrite each
      row's accurate, informative `reason` field for no real benefit (PR
      #226 already documents this).
- [x] Recorded this independent reconfirmation via
      `agent_work_briefing.py record-completion` against this task's own
      UMR (`UMR-20260806-102610-1930`), citing PR #226/#227 as the real,
      already-in-flight evidence rather than duplicating it.

## Remaining
- [ ] Owner decision needed (surfaced, not guessed): what to do about the
      already-provisioned `meridian-test-industries-gtm-fixture-nonprod`
      tenant and its 4 real role credentials, created and used without a
      real Owner authorization artifact, and about the direct-push-to-main
      process gap (commits `7255124`/`d41ff9b`, no PR, author
      `VERIDIAN-DEV Ops`) that let it happen. See the new
      `pm_decisions_pending` row for full detail.
- [ ] PR #226 (open, `UMR-20260806-093654-7566`) should be merged by its
      own owning task/process -- not superseded or duplicated by this one.
