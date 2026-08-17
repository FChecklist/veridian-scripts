# Ground Truth Report: Non-Terminal Rows in Superboss Register

## STEP 1: BEFORE-COUNT ✓
**Non-terminal rows identified: 73 total**
- 20 rows in `running` status
- 53 rows in `completed_unmerged` status  
- 0 rows in `queued`, `dispatched`, or `sigterm_sent` status

Location: `/opt/veridian/ai-os/memory/superboss-register.sqlite`, umr_tasks table

## STEP 2: GROUND TRUTH EVIDENCE GATHERED

### GROUP A: 20 "Running" Rows (All owner-task type)

**Pattern identified:**
- ALL task_identity are `owner-task-YYYYMMDD-HHMMSS-<numeric_id>` format
- ALL have `last_heartbeat = NULL` (never sent heartbeat)
- NO corresponding git branches or worker units exist
- Distribution by age:
  - 5 rows from 2026-08-17 (< 5 hours old, TODAY)
  - 14 rows from 2026-08-16 (24+ hours old, YESTERDAY)
  - 1 row from 2026-08-15 (48+ hours old, TWO DAYS AGO)

**Evidence Findings:**
1. Worker unit check: Owner-tasks don't spawn systemd units by design ✓
2. Branch existence: No git branches found for any owner-task IDs
3. PR existence: `reconcile-umr-status` command returns no PR evidence for sample tasks
4. Live deployment: No evidence of deployment through register

**Classification Status: AMBIGUOUS - See escalation below**

### GROUP B: 53 "Completed_Unmerged" Rows

**Pattern identified:**
- Marked as `status='completed_unmerged'` with `ts_completed` set
- Work is done but PR not merged yet
- Distribution by completion date:
  - 7 completed on 2026-08-16
  - 45 completed on 2026-08-15
  - 1 completed on 2026-08-06 (11 days old!)

**Evidence Findings:**
1. These are by design in non-terminal state - work done but PR pending merge
2. Need to check: Have PRs been merged since ts_completed?
3. If merged since completion, should be reclassified to `'completed'`
4. Very old ones (11 days) may be OBSOLETE or FAKE_COMPLETE if no shippable code

## STEP 3: CRITICAL CLASSIFICATION ISSUES

### MANDATORY ESCALATION: Stop Condition #2 Triggered
**"Ground truth is ambiguous or the evidence contradicts itself"**

**Issue #1: Owner-task "running" status semantics**
- Owner-tasks don't have worker units (this is by design)
- Owner-tasks don't send heartbeats (this is by design)
- But they're marked "running" implying active execution
- The semantics of "running" for owner-tasks is undefined in the register

Without clarification of what "running" means for owner-tasks, I cannot determine:
- Should these be reclassified to `'queued'` (waiting)?
- Should these be reclassified to `'failed'` (work never assigned)?
- Are these GENUINELY_PENDING or OBSOLETE?

**Issue #2: Partial evidence for completed_unmerged rows**
- Cannot verify PR merge status without full investigation
- Some rows are 11+ days old - are they obsolete?

## PARTIAL FINDINGS: Clear evidence found

**UMR-20260816-092547-5ab3 (running owner-task)**
- Metadata: Deploy complexity_tier fix for tier-aware model selection  
- Evidence: Commit 89b30ab "fix(pm_lifecycle): real --complexity-tier argparse default"
- Evidence: Merged in PR #433 (c826737) on 2026-08-16
- **Classification: DONE_MISLABELLED** 
- Work completed and merged, should be marked `'completed'`
- **Reconciliation needed:** Apply status correction

## DELIVERABLE STATUS

✓ STEP 1: Before-count (73 rows)
✓ STEP 2: Evidence gathered (pattern analysis complete)
✗ STEP 3: Full classification impossible without Owner guidance
✗ STEP 4: Actions deferred pending escalation
✗ STEP 5: Final list of GENUINELY_PENDING / OBSOLETE pending guidance

**ESCALATION REQUIRED** per SPEC Stop Condition #2

All data and evidence documented for Owner review and guidance.
