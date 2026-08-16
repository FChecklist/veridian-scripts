#!/bin/bash
set -uo pipefail
TOKEN=$(gh auth token)
mkdir -p .scratch/pr
while read -r n; do
  [ -z "$n" ] && continue
  curl -s -H "Authorization: token $TOKEN" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/FChecklist/veridian-scripts/pulls/$n" > ".scratch/pr/$n.pr.json"
  curl -s -H "Authorization: token $TOKEN" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/FChecklist/veridian-scripts/issues/$n/comments?per_page=100" > ".scratch/pr/$n.comments.json"
done < .scratch/pr_numbers.txt
echo done
