#!/bin/bash
DIRS="
/opt/veridian/ai-os/tasks/task-20260816-121753-adopted-sweep-reaudit-veridian-scripts-424-pm-se
/opt/veridian/ai-os/tasks/task-20260816-121802-adopted-sweep-reaudit-veridian-scripts-357-regis
/opt/veridian/ai-os/tasks/task-20260816-121810-adopted-sweep-reaudit-veridian-scripts-355-pm-se
/opt/veridian/ai-os/tasks/task-20260816-121815-adopted-sweep-reaudit-veridian-scripts-198-pm-re
/opt/veridian/ai-os/tasks/task-20260816-121821-adopted-sweep-reaudit-veridian-scripts-79-gtm-ui
/opt/veridian/ai-os/tasks/task-20260816-121826-adopted-sweep-reaudit-veridian-scripts-65-gtm-db
/opt/veridian/ai-os/tasks/task-20260816-121832-adopted-sweep-reaudit-veridian-scripts-61-ocid-m
/opt/veridian/ai-os/tasks/task-20260816-121838-adopted-sweep-reaudit-veridian-scripts-8-coordin
"
while true; do
  all_done=1
  for d in $DIRS; do
    if [ ! -f "$d/review.json" ]; then
      all_done=0
    fi
  done
  if [ "$all_done" -eq 1 ]; then
    echo "ALL 8 REVIEWS COMPLETE"
    break
  fi
  sleep 15
done
