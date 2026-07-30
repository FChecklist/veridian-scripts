#!/bin/bash
# Force-kills the isolated Chrome sandbox. Called manually by the OWNER, and
# automatically by resource_governor.py if server CPU/RAM crosses 95%.
# Uses `docker stop` (not `down -v`) so the profile volume -- and therefore
# saved logins -- survives; the container itself does not persist any state
# of its own outside that volume.
set -e
docker stop --timeout 5 isolated_chrome 2>&1 || echo "isolated_chrome was not running."
echo "isolated_chrome stopped. Profile volume preserved at /opt/veridian/isolated_chrome/profile."
