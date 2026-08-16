#!/bin/bash
set -e
OUT=.scratch/pr_info.txt
> "$OUT"
while read -r n; do
  [ -z "$n" ] && continue
  mergeable=$(gh pr view "$n" --repo FChecklist/veridian-scripts --json mergeable -q .mergeable)
  headRef=$(gh pr view "$n" --repo FChecklist/veridian-scripts --json headRefName -q .headRefName)
  updatedAt=$(gh pr view "$n" --repo FChecklist/veridian-scripts --json updatedAt -q .updatedAt)
  echo "$n|$mergeable|$updatedAt|$headRef" >> "$OUT"
done < .scratch/pr_numbers.txt
cat "$OUT"
