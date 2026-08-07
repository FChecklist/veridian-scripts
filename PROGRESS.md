# PROGRESS -- task-20260807-052106-build-real-deterministic-umr-percent-com

SPEC: governing chain UMR-20260806-124055-bc80. Build a real, deterministic
per-umr_id percent-completion script (never AI-judged), test it against 5
real UMR IDs, register it in capability_registry.

## Completed
- [x] Verified precedent: 17 capability_registry rows, none is a per-UMR
      percent calculator (`lookup-capability --intent-text` search); confirmed
      genuinely new.
- [x] Confirmed live DB path resolution: `/opt/veridian/ai-os/memory/superboss-register.sqlite`
      (via `resolve_superboss_db_path()`), NOT `/opt/veridian/scripts/superboss-register.sqlite`
      -- avoided the known wrong-DB-file trap.
- [x] Read real `umr_tasks` schema + `task.yaml` conventions (`completed_steps`/
      `remaining_steps` top-level lists, `outputs_json.new_task_id` as the real
      dispatched task-dir name) from `resource_governor.py`/`veridian-task.py`/
      `backfill_phase_self_report.py`.
- [x] Built `umr_completion_percentage.py`: 5 deterministic rules, evaluated in
      fixed order, unresolved cases emit `percent: null` + a real reason string
      instead of guessing.
- [x] Ran it against all 5 required real UMR IDs against the live DB -- real
      output pasted below.
- [x] Registered `umr_completion_percentage` in `capability_registry` citing
      UMR-20260806-124055-bc80 -- new capability_id: `CAP-20260807-052805-fb1a`
      (verified live via `lookup-capability --capability-name`).
- [x] Recorded completion via `agent_work_briefing.py record-completion`.

## Remaining
- [ ] (none)

## Real test output (5 required UMR IDs, run against the live DB)

```json
[
  {
    "umr_id": "UMR-20260806-135632-329e",
    "percent": 100,
    "rule": "rule1_completed_with_evidence",
    "reason": "status='completed', ts_completed='2026-08-07T00:44:23.201992+00:00' is a real non-null timestamp, outputs_json has real non-empty evidence field(s): ['commit_sha', 'pr_number', 'repo']"
  },
  {
    "umr_id": "UMR-20260806-140841-46d1",
    "percent": 100,
    "rule": "rule1_completed_with_evidence",
    "reason": "status='completed', ts_completed='2026-08-06T19:39:25.057964+00:00' is a real non-null timestamp, outputs_json has real non-empty evidence field(s): ['new_task_id', 'returncode', 'stderr']"
  },
  {
    "umr_id": "UMR-20260806-141055-1fec",
    "percent": 17,
    "rule": "rule2_task_yaml_steps",
    "reason": "real task.yaml found at tasks/task-20260806-193955-deterministic-final-audit--zero-gap-zero/task.yaml (matched via new_task_id='task-20260806-193955-deterministic-final-audit--zero-gap-zero', status='completed'): len(completed_steps)=1, len(remaining_steps)=5, total=6"
  },
  {
    "umr_id": "UMR-20260806-171945-5767",
    "percent": 33,
    "rule": "rule2_task_yaml_steps",
    "reason": "real task.yaml found at tasks/task-20260806-201941-single-deterministic-orchestrator--one-e/task.yaml (matched via new_task_id='task-20260806-201941-single-deterministic-orchestrator--one-e', status='failed'): len(completed_steps)=1, len(remaining_steps)=2, total=3"
  },
  {
    "umr_id": "UMR-20260807-024922-f432",
    "percent": 80,
    "rule": "rule5_running_task_yaml_steps",
    "reason": "real task.yaml found at tasks/task-20260807-052031-rca-confirmed---interference-removed--no/task.yaml (matched via new_task_id='task-20260807-052031-rca-confirmed---interference-removed--no', status='running'): len(completed_steps)=8, len(remaining_steps)=2, total=10"
  }
]
```

Notable finding worth flagging honestly: none of the 5 real test UMRs hit
rule 3 (failed, no task.yaml), rule 4 (queued, undispatched), or the fully
unresolved fallback (rule 1's own `UMR-20260806-141055-1fec` almost hit it --
`status='completed'` but `ts_completed` is NULL on that row -- but its
task.yaml existed with real steps, so rule 2 resolved it instead). The
unresolved/null path is implemented and covered by the standalone test file
(`test_umr_completion_percentage.py`), not by these 5 live rows.
