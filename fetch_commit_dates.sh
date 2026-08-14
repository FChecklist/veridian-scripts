#!/bin/bash
set -euo pipefail
OUT=commit_dates.jsonl
> "$OUT"
for n in 385 384 371 247 244 233 232 205 200 198 118 99 93 90 71 61 60; do
  sha=$(python3 -c "
import json
with open('pr_state.jsonl') as f:
    for line in f:
        d = json.loads(line)
        if d['number'] == $n:
            print(d['head_sha'])
            break
")
  date=$(gh api "repos/FChecklist/veridian-scripts/commits/$sha" -q '.commit.committer.date' 2>>fetch_commit_errs.txt || echo "ERROR")
  echo "{\"number\": $n, \"head_sha\": \"$sha\", \"commit_date\": \"$date\"}" >> "$OUT"
done
