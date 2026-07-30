#!/bin/bash
# Force-kills the pallavi-tracker standalone pocket app (screen session
# "pallavi_app"). Called manually by the OWNER, and automatically by
# resource_governor.py if server CPU/RAM crosses PALLAVI_GOVERNOR_KILL_THRESHOLD_PERCENT.
# The sqlite DB at /opt/veridian/apps/pallavi-tracker/pallavi.db is untouched --
# only the running app process is stopped. Restart with:
#   screen -dmS pallavi_app bash -c 'cd /opt/veridian/apps/pallavi-tracker && ./venv/bin/python3 app.py'
set -e
screen -S pallavi_app -X quit 2>&1 || echo "pallavi_app screen session was not running."
echo "pallavi_app stopped. Data preserved at /opt/veridian/apps/pallavi-tracker/pallavi.db."
