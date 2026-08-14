#!/bin/bash
set -euo pipefail
OUT=pr_state.jsonl
> "$OUT"
while read -r n; do
  echo "fetching $n" >&2
  gh api "repos/FChecklist/veridian-scripts/pulls/$n" -q '{number:.number, title:.title, head_sha:.head.sha, head_ref:.head.ref, mergeable:.mergeable, mergeable_state:.mergeable_state, updated_at:.updated_at, draft:.draft}' >> "$OUT" 2>>fetch_errs.txt || echo "{\"number\":$n,\"error\":true}" >> "$OUT"
done < pr_numbers.txt
