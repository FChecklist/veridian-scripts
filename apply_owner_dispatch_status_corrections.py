#!/usr/bin/env python3
"""apply_owner_dispatch_status_corrections.py -- applies already-computed,
already-evidenced `umr_tasks.status` corrections that a prior real
investigation (UMR-20260806-081403-ebd3, the owner_dispatch_gateway
mislabeled-'failed' root-cause investigation that also produced the real
task.yaml+gh cross-check fix landed in resource_governor.py's
backfill_null_heartbeats()/_forward_progress_decision(), PR #148) computed
and recorded per-row in `metadata_json` but never wrote back to the real
`status` column.

Root cause this closes (UMR-20260806-142206, "fix real umr_tasks status
equals failed" dispatch): that prior investigation was scoped to
propose-then-apply -- it gathered real evidence (task.yaml status, real
pushed commits, real `gh pr view` state, real credit-accountant/reviewer-
gate blockers) for every real owner_dispatch_gateway row it examined and
recorded a `corrected_status` value under a namespaced
`reconciliation_UMR-20260806-081403-ebd3_evidence` / `corrected_status`
key inside that row's own `metadata_json` -- but nothing ever executed the
second half (the real `status` column write). This script IS that second
half: it reads back the evidence that investigation already gathered and
verified (never re-narrates or re-guesses it), and applies it through the
one real, canonical write path every other reconciliation script in this
codebase already uses -- `superboss-register.py`'s own `update_umr_task()`,
inside its own `_write_lock()`, never a raw `UPDATE umr_tasks ...`
statement (enforced structurally below: this script's only DB write call
is through `_sbr.update_umr_task()`).

Read-merge-write on metadata_json, not a blind replace, matching
reconcile_owner_dispatch_status.py's apply_correction() / PR #147's fix --
the existing `reconciliation_UMR-20260806-081403-ebd3_evidence` /
`corrected_status` keys (and any other real key already in the row's
metadata) are preserved untouched; this script only adds one further
namespaced `applied_by`/`applied_at` marker so a second run is a real,
observable no-op.

Scope: EVERY real umr_tasks row carrying a `corrected_status` key in its
metadata_json whose current `status` column does not yet match it --
deliberately not narrowed to the 24h owner_dispatch_gateway window or to
any specific source_trigger, because the prior investigation's own
per-row evidence keys are the real, already-verified selection signal,
not a wall-clock window (a row that ages past 24h between the
investigation and this apply step must still get its real correction).

A row whose current `status` already equals its own `corrected_status`
(the prior investigation already/independently converged, e.g. via
duplicate-detection setting `rejected_duplicate` naturally) is correctly
skipped as a real no-op, not re-written.

Usage:
    python3 apply_owner_dispatch_status_corrections.py                # report only (default, no writes)
    python3 apply_owner_dispatch_status_corrections.py --apply         # write corrections via update_umr_task()
    python3 apply_owner_dispatch_status_corrections.py --json out.json # write full per-row evidence to a file
    python3 apply_owner_dispatch_status_corrections.py --umr-id UMR-...  # limit to one row (debugging/re-run)

Exit codes: 0 = ran clean. 2 = real internal error (DB path, subprocess, etc.).
"""
import argparse
import importlib.util as _ilu
import json
import os
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

_sbr_spec = _ilu.spec_from_file_location("superboss_register", os.path.join(SCRIPT_DIR, "superboss-register.py"))
_sbr = _ilu.module_from_spec(_sbr_spec)
_sbr_spec.loader.exec_module(_sbr)

THIS_SCRIPT = "apply_owner_dispatch_status_corrections.py"


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def find_pending_corrections(conn, umr_id=None):
    """Real scan for rows carrying a `corrected_status` key anywhere in
    metadata_json whose current `status` differs from it. No text
    matching/guessing -- both values come straight from the row's own real
    columns."""
    if umr_id:
        cur = conn.execute("SELECT umr_id, status, reason, metadata_json FROM umr_tasks WHERE umr_id=?", (umr_id,))
    else:
        cur = conn.execute("SELECT umr_id, status, reason, metadata_json FROM umr_tasks WHERE metadata_json LIKE '%corrected_status%'")
    pending = []
    for row in cur.fetchall():
        d = dict(row)
        try:
            metadata = json.loads(d["metadata_json"]) if d["metadata_json"] else {}
        except Exception:
            continue
        corrected_status = None
        evidence_key = None
        evidence_text = None
        for key, value in metadata.items():
            if key == "corrected_status":
                corrected_status = value
            elif isinstance(value, dict) and "corrected_status" in value:
                corrected_status = value["corrected_status"]
                evidence_key = key
                evidence_text = value.get("evidence") if "evidence" in value else value
        if corrected_status is None:
            continue
        if evidence_key is None:
            # corrected_status was a top-level key -- the real evidence
            # narrative sits in a real sibling `reconciliation_*_evidence`
            # key (string), never re-derived, never guessed.
            for key, value in metadata.items():
                if key.startswith("reconciliation_") and key.endswith("_evidence"):
                    evidence_key = key
                    evidence_text = value
                    break
            if evidence_key is None:
                evidence_key = "corrected_status"
                evidence_text = metadata.get("reason_for_correction")
        if corrected_status == d["status"]:
            continue  # already converged -- real no-op, not re-written
        pending.append({
            "umr_id": d["umr_id"],
            "current_status": d["status"],
            "corrected_status": corrected_status,
            "evidence_key": evidence_key,
            "evidence": evidence_text,
            "prior_reason": d["reason"],
            "metadata": metadata,
        })
    return pending


def apply_one(conn, item):
    """Writes exactly one real correction through update_umr_task() ONLY.
    Read-merge-write on metadata_json so every real pre-existing key
    (including the investigation's own `reconciliation_..._evidence` key)
    survives untouched."""
    existing_row = conn.execute(
        "SELECT metadata_json FROM umr_tasks WHERE umr_id=?", (item["umr_id"],)
    ).fetchone()
    existing_metadata = {}
    if existing_row and existing_row["metadata_json"]:
        try:
            existing_metadata = json.loads(existing_row["metadata_json"])
        except Exception:
            existing_metadata = {"_unparseable_prior_metadata_json": existing_row["metadata_json"]}
    merged_metadata = dict(existing_metadata)
    merged_metadata[f"applied_{THIS_SCRIPT}"] = {
        "applied_at": _now_iso(),
        "prior_status": item["current_status"],
        "new_status": item["corrected_status"],
        "evidence_key_used": item["evidence_key"],
    }
    reason_text = item["evidence"] if isinstance(item["evidence"], str) else json.dumps(item["evidence"])
    _sbr.update_umr_task(
        conn, item["umr_id"],
        status=item["corrected_status"],
        reason=reason_text or item["prior_reason"],
        metadata=merged_metadata,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write corrections back via update_umr_task()")
    ap.add_argument("--json", dest="json_out", default=None, help="write full per-row evidence to a file")
    ap.add_argument("--umr-id", dest="umr_id", default=None, help="limit to one row (debugging/re-run)")
    args = ap.parse_args()

    conn = _sbr._connect()
    pending = find_pending_corrections(conn, umr_id=args.umr_id)

    print(f"Rows with a real, already-evidenced corrected_status pending real status-column apply: {len(pending)}")
    for item in pending:
        print(f"  {item['umr_id']}: {item['current_status']!r} -> {item['corrected_status']!r}")
        ev = item["evidence"]
        ev_text = ev if isinstance(ev, str) else json.dumps(ev)
        print(f"      evidence: {(ev_text or '')[:220]}")

    if args.apply and pending:
        with _sbr._write_lock():
            for item in pending:
                apply_one(conn, item)
            conn.commit()
        print(f"Applied {len(pending)} real correction(s) via update_umr_task().")
    elif not args.apply:
        print("(dry run -- pass --apply to write)")

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(pending, f, indent=2, default=str)

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
