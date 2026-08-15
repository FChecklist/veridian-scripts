#!/usr/bin/env python3
"""
Timer Manager -- control Veridian systemd --user timers
"""

import os
import sys
import re
import json
import argparse
import subprocess
import tempfile
from datetime import datetime, timezone

USER_CONFIG = os.path.expanduser("~/.config/systemd/user")
DEFAULT_TIMER_PATH = "/usr/lib/systemd/system/timer"


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    return proc.stdout, proc.stderr, proc.returncode


def get_timer_path(timer_name):
    path = os.path.join(USER_CONFIG, timer_name + ".timer")
    if os.path.exists(path):
        return path
    return None


_LISTING_SUMMARY_RE = re.compile(r'^\d+ timers? listed\.$')


def list_timers(show_all=False):
    """Bug fix (reproduced live 2026-08-15 against the real currently-stopped
    veridian-*.timer units): this used to fetch every timer unconditionally
    and filter client-side by slicing line.split()[4], on the assumption that
    the NEXT and LEFT columns are always populated with a real
    date+time+duration triple. When a timer is currently stopped, systemctl
    prints a bare "-" for NEXT and LEFT instead of that triple, which shortens
    the token count for that line and shifts every later column left -- so
    parts[4] silently landed on a LAST-column token (a time-of-day, never the
    unit name) instead of UNIT, the "veridian-" prefix check on that wrong
    token always failed, and the tool printed nothing at all instead of
    erroring or falling back. Live repro: every real veridian-*.timer unit on
    this box is currently stopped, so the old code printed zero rows.

    Fixed two ways at once (both suggested by the governing SPEC):
      1. Filter server-side with an explicit unit-name PATTERN passed
         straight to systemctl (list-timers accepts unit-glob patterns, same
         as list-units) -- so which timers come back no longer depends on
         parsing any column at all.
      2. Never index into split() columns to find the unit name: matching
         lines are printed verbatim (after dropping the header and the
         trailing "N timers listed." summary line), so a stopped timer's
         bare "-  -" NEXT/LEFT columns never need to be parsed -- the unit
         name is simply part of the line already being printed.
    """
    stdout, stderr, rc = _run([
        "systemctl", "--user", "list-timers", "--all", "--no-pager", "veridian-*.timer",
    ])
    if rc != 0:
        print("Error fetching timers", file=sys.stderr)
        return
    lines = stdout.strip().splitlines()
    if not lines:
        return
    for line in lines[1:]:  # drop header row
        line = line.strip()
        if not line or _LISTING_SUMMARY_RE.match(line):
            continue
        print(line)


def ctl(cmd, args):
    out, err, rc = _run(["systemctl", "--user", cmd] + args)
    if rc != 0:
        print(err, file=sys.stderr)
    else:
        print(out)


def modify_oncalendar(timer_name, new_value):
    path = get_timer_path(timer_name)
    if not path:
        print("Timer file not found:", timer_name, file=sys.stderr)
        return
    with tempfile.NamedTemporaryFile(mode='w+', dir=os.path.dirname(path), delete=False) as f:
        with open(path, 'r') as src:
            content = src.read()
        new_content, num = re.subn(r'^(OnCalendar=.*)$', f'OnCalendar={new_value}', content, count=1, flags=re.MULTILINE)
        if num == 0:
            print("Warning: No OnCalendar= line found; adding new line.")
            new_content = content + "\nOnCalendar=" + new_value + "\n"
        else:
            new_content = re.sub(r'^OnCalendar=.*$', f'OnCalendar={new_value}', new_content, count=1, flags=re.MULTILINE)
        f.write(new_content)
        f.flush()
        os.fsync(f.fileno())
        os.rename(f.name, path)
    ctl("daemon-reload", [])
    ctl("restart", [timer_name + ".timer"])
    print(f"Updated OnCalendar of {timer_name} to {new_value}")


def main():
    parser = argparse.ArgumentParser(description='Veridian Timer Manager')
    subparsers = parser.add_subparsers(dest='command', required=True)

    p_list = subparsers.add_parser('list', help='List all Veridian timers')
    p_list.set_defaults(func=lambda a: list_timers())

    p_stop = subparsers.add_parser('stop', help='Stop a timer')
    p_stop.add_argument('timer')
    p_stop.set_defaults(func=lambda a: ctl("stop", [a.timer + ".timer"]))

    p_start = subparsers.add_parser('start', help='Start a timer')
    p_start.add_argument('timer')
    p_start.set_defaults(func=lambda a: ctl("start", [a.timer + ".timer"]))

    p_disable = subparsers.add_parser('disable', help='Remove enable symlink')
    p_disable.add_argument('timer')
    p_disable.set_defaults(func=lambda a: ctl("disable", [a.timer + ".timer"]))

    p_enable = subparsers.add_parser('enable', help='Create enable symlink')
    p_enable.add_argument('timer')
    p_enable.set_defaults(func=lambda a: ctl("enable", [a.timer + ".timer"]))

    p_pause = subparsers.add_parser('pause', help='Mask timer (prevents any start)')
    p_pause.add_argument('timer')
    p_pause.set_defaults(func=lambda a: ctl("mask", [a.timer + ".timer"]))

    p_unpause = subparsers.add_parser('unpause', help='Unmask timer')
    p_unpause.add_argument('timer')
    p_unpause.set_defaults(func=lambda a: ctl("unmask", [a.timer + ".timer"]))

    p_reschedule = subparsers.add_parser('reschedule', help='Change OnCalendar schedule')
    p_reschedule.add_argument('timer')
    p_reschedule.add_argument('--cron', required=True, help='New OnCalendar value, e.g. "*:0/5"')
    p_reschedule.set_defaults(func=lambda a: modify_oncalendar(a.timer, a.cron))

    p_set_once = subparsers.add_parser('set-once', help='Set timer to fire once at a specific time')
    p_set_once.add_argument('timer')
    p_set_once.add_argument('--at', required=True, help='Time in format "YYYY-MM-DD HH:MM:SS"')
    p_set_once.set_defaults(func=lambda a: modify_oncalendar(a.timer, a.at))

    p_status = subparsers.add_parser('status', help='Show timer status')
    p_status.add_argument('timer')
    p_status.set_defaults(func=lambda a: ctl("status", [a.timer + ".timer"]))

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()