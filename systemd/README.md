# Tracked systemd --user unit templates

These are version-controlled reference copies of live unit files that
otherwise only exist, un-versioned, under
`~/.config/systemd/user/` on VERIDIAN-DEV. There is no automated deploy
step for this directory (unlike the .py scripts in the parent dir, which
ARE the live files -- this repo's working tree at /opt/veridian/scripts IS
what runs) -- a change here must still be copied to
`~/.config/systemd/user/<name>` by hand, followed by
`systemctl --user daemon-reload`, same as before this directory existed.
Added 2026-08-01 alongside the 24-unit OOM-kill RCA fix (see veridian-task.py
cmd_create and dispatch-tick.py's resume_interrupted_workers_tick) so the
worker unit template's own root-cause fix (removing [Install]/WantedBy=
default.target) is reviewable in the same PR as the code that depends on it,
instead of being an invisible, SSH-only edit no PR ever showed.
