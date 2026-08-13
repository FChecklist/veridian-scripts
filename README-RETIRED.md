# This directory is retired

`scripts/` in this repo used to be the deployment source for the live
`/opt/veridian/scripts` directory on VERIDIAN-DEV, via `sync-repos.sh` +
`deploy-live-scripts.sh` (see `SCRIPTS_LIVE_VS_REPO_DRIFT_AUDIT_2026-07-25.yaml`
in the ai-os repo for the original reasoning).

As of 2026-08-01 this is retired. Root cause found and fixed the same day:
`/opt/veridian/scripts` is itself a real git working copy of
[`FChecklist/veridian-scripts`](https://github.com/FChecklist/veridian-scripts),
but `deploy-live-scripts.sh` was unconditionally overwriting same-named
tracked files there with this directory's older content on every sync
cycle — silently discarding real fixes merged into `veridian-scripts`.
Confirmed concretely: the 2026-07-27 worker-boot-activation OOM fix and
`dispatch-tick.py`'s `resume_interrupted_workers_tick` never actually
reached production despite being merged, because of this.

`sync-repos.sh` now pulls `/opt/veridian/scripts` directly from
`veridian-scripts` instead. This directory is no longer read by anything.

**Do not add or edit files here for anything meant to run on the server.**
Use [`FChecklist/veridian-scripts`](https://github.com/FChecklist/veridian-scripts)
instead. The two files that existed only here
(`claude-tmux-usage-limit-check.sh`, `claude-usage-limit-retry.sh`) have
already been migrated there.
