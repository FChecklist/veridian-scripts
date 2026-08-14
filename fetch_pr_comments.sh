#!/bin/bash
set -euo pipefail
OUT=pr_comments.jsonl
> "$OUT"
while read -r n; do
  echo "fetching comments $n" >&2
  gh api "repos/FChecklist/veridian-scripts/issues/$n/comments" -q '[.[] | select(.body | test("AUDIT"; "i")) | {id, created_at, user: .user.login, body}]' > tmp_comment_out.json 2>>fetch_comment_errs.txt || echo "[]" > tmp_comment_out.json
  python3 -c "
import json
n = $n
try:
    with open('tmp_comment_out.json') as f:
        data = json.load(f)
except Exception as e:
    data = []
print(json.dumps({'number': n, 'audit_comments': data}))
" >> "$OUT"
done < pr_numbers.txt
