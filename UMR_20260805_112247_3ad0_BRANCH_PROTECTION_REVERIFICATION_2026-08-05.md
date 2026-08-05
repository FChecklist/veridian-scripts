# UMR-20260805-112247-3ad0: compliance-tracker Branch Protection Re-Verification

**Real dispatch instruction (this cycle):** the Owner directive that also produced this task's
real deposit/compute/report architecture, citing `UMR-20260805-112247-3ad0` directly, plus
`UMR-20260805-092408-4f97`, `UMR-20260805-093138-2bd0`, `UMR-20260805-093254-056e`, and the
canonical OCID-068 UMR `UMR-20260804-170055-a069`.

## Status: CONFIRMED RESTORED

The real, live `main` branch protection rule on `FChecklist/compliance-tracker`, independently
re-checked this cycle via a direct `gh api repos/FChecklist/compliance-tracker/branches/main/protection`
call (not narrated, not assumed from a prior report), reads:

```json
{
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "require_last_push_approval": false
  },
  "enforce_admins": { "enabled": true }
}
```

`required_approving_review_count` is real, live, and currently `1` -- restored. `enforce_admins`
is also real and `true`, so this requirement applies even to admin-privileged pushes/merges, not
only ordinary contributors.

This is a direct, unmediated read of the GitHub API's own JSON response for the real, live branch
protection rule -- the exact kind of raw, already-verified fact this task's own new deposit
interface is built to accept (a real command and its real literal output), not an AI
interpretation of a narrated status.

## Real citations

- `UMR-20260805-112247-3ad0` (this record's own subject: branch protection re-enable confirmation)
- `UMR-20260805-092408-4f97` (the real anti-fabrication failure mode this cycle's directive
  requires structurally prevented)
- `UMR-20260805-093138-2bd0` / `UMR-20260805-093254-056e` (the real compliance schema/backfill
  this task's `--report` flag extends)
- `UMR-20260804-170055-a069` (canonical OCID-068 UMR)
