# Tracked host config (not deployed live by this PR)

Version-controlled reference copies of real host `/etc` config, same "no
automated deploy step for this directory" convention as `../../systemd/`
(see `systemd/README.md`) -- a change here must still be copied to its real
`/etc` path by hand and the relevant service restarted, and this repo's own
CI/deploy has no `sudo` access to do that automatically.

Added 2026-08-14 (UMR-20260814-033442-c885, P0 disk exhaustion RCA) for the
task spec's own item 5 ("cap the second unbounded grower found on the same
volume"). NOT applied live as of this PR -- confirmed at the time this PR
was authored that the authoring session had no passwordless `sudo` on this
host, so it could not apply these itself; a human (or a session with real
sudo) must deploy them.

## journald.conf.d/veridian-disk-cap.conf

Deploy: `sudo cp config/host/journald.conf.d/veridian-disk-cap.conf /etc/systemd/journald.conf.d/veridian-disk-cap.conf && sudo systemctl restart systemd-journald`

Real measured before-state: `journalctl --disk-usage` reported 3.6G with no
`SystemMaxUse` set at all in the live `/etc/systemd/journald.conf` (the
line was present but commented out).

## logrotate.d/rsyslog

Deploy: `sudo cp config/host/logrotate.d/rsyslog /etc/logrotate.d/rsyslog` (overwrites the live file in place -- see the file's own header for why this can't instead be a same-named sibling file, and for the real live-content diff).

Real measured before-state: `/var/log/syslog` 7510MB, `/var/log/syslog.1`
1552MB, live config `weekly, rotate 4`, no size cap of any kind.
