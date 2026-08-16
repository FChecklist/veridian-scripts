#!/bin/bash
set -uo pipefail
declare -A B
B[424]="worker/task-20260815-114156-pm-in-server--add-real-part3-4-gtm-cert"
B[357]="fix/register-path-resolution-decoy-cleanup-umr20260813130245-6a26"
B[355]="fix/pm-sentinel-tick-real-token-delta-guard-10c3"
B[198]="worker/task-20260806-165917-extend-deterministic-report-to-cover-pr"
B[79]="feat/gtm-checks-ui-e2e-testing"
B[65]="feat/gtm-checks-db-api-governance-umr20260805153813"
B[61]="feat/ocid-master-standard-v6-phase2-lifecycle-registry-integrity"
B[8]="feat/coordination-graph-entity-relation"
for n in 424 357 355 198 79 65 61 8; do
  br="${B[$n]}"
  echo "=== PR $n branch $br ==="
  mb=$(git merge-base "origin/$br" origin/main 2>&1)
  echo "merge-base: $mb"
  tree=$(git merge-tree --write-tree "origin/$br" origin/main 2>&1)
  echo "$tree" | grep -c "CONFLICT" || true
  echo "$tree" | grep "CONFLICT" | head -10
  echo ""
done
