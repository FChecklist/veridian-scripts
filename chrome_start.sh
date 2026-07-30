#!/bin/bash
# Starts the isolated Chrome sandbox. On-demand only -- nothing calls this
# automatically (no cron/systemd timer registers it; see chrome_governance
# check in resource_governor.py for the enforcement side of that rule).
set -e
cd /opt/veridian/isolated_chrome
docker compose up -d --build
echo "isolated_chrome started. noVNC: http://127.0.0.1:6080/vnc.html (SSH-tunnel this port from your laptop)."
echo "To view from your laptop: ssh -L 6080:127.0.0.1:6080 rajat@167.233.220.35"
