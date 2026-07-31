#!/usr/bin/env python3
"""
test_dedup_constraints_2026-07-31.py -- standalone (no pytest required) proof
that task-20260731-074406's structural duplicate-prevention constraints are
real, not just documented: inserts the same content_hash+artifact_path twice
via superboss-register.py's real register-knowledge CLI, then claims the same
task_key twice via its real claim-task-key CLI, and asserts the second call
of each pair is rejected via a real sqlite3.IntegrityError (surfaced as a
structured JSON "duplicate"/"claimed": false signal, not a crash) rather than
silently succeeding.

Runs entirely against a throwaway temp DB (SUPERBOSS_REGISTER_DB env var
override, same mechanism superboss-register.py's own DB_PATH already
supports) -- never touches the live
/opt/veridian/ai-os/memory/superboss-register.sqlite.

Run: python3 test_dedup_constraints_2026-07-31.py
Exits 0 and prints PASS if every check holds; exits 1 and prints the first
failure otherwise.
"""
import json
import os
import subprocess
import sys
import tempfile

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
SUPERBOSS = os.path.join(SCRIPTS, "superboss-register.py")
MIGRATE = os.path.join(SCRIPTS, "migrate_2026-07-31_dedup_constraints.py")


def run(cmd, env):
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def main():
    tmpdir = tempfile.mkdtemp(prefix="dedup_constraint_test_")
    db_path = os.path.join(tmpdir, "test.sqlite")
    env = dict(os.environ)
    env["SUPERBOSS_REGISTER_DB"] = db_path

    failures = []

    # 1. Fresh schema.
    proc = run(["python3", SUPERBOSS, "init"], env)
    if proc.returncode != 0:
        failures.append(f"init failed: {proc.stderr}")

    # 2. Apply the real migration (unique index + task_claims table) against
    #    this throwaway DB -- exercises the actual migration script, not a
    #    reimplementation of its logic.
    proc = run(["python3", MIGRATE], env)
    if proc.returncode != 0:
        failures.append(f"migration failed: {proc.stderr}")
    else:
        migrate_result = json.loads(proc.stdout)
        steps = {s["step"] for s in migrate_result["log"]}
        if "create_unique_index" not in steps:
            failures.append(f"migration did not report create_unique_index: {migrate_result}")
        if "create_task_claims_table" not in steps:
            failures.append(f"migration did not report create_task_claims_table: {migrate_result}")

    if failures:
        print("FAIL (setup):\n" + "\n".join(failures))
        return 1

    # 3. Register a real artifact twice with the identical content_hash-
    #    producing content + identical --path -- same shape as the 2 real
    #    duplicate incidents found live in knowledge_engine
    #    (WIRING_ENGINE_PHASE_PLAN_2026-07-25.yaml, VERIDIAN_V2_DSPY_TECH_
    #    DECISION_2026-07-27.md: identical content_hash+artifact_path,
    #    registered twice by mistake).
    artifact_file = os.path.join(tmpdir, "artifact.txt")
    with open(artifact_file, "w") as f:
        f.write("duplicate-constraint-test-content\n")

    regk_cmd = ["python3", SUPERBOSS, "register-knowledge",
                "--path", artifact_file, "--artifact-type", "canonical",
                "--purpose", "dedup constraint test artifact"]
    first = run(regk_cmd, env)
    if first.returncode != 0:
        failures.append(f"first register-knowledge call unexpectedly failed: {first.stdout} {first.stderr}")
    else:
        first_result = json.loads(first.stdout)
        if "artifact_id" not in first_result:
            failures.append(f"first register-knowledge call did not return artifact_id: {first_result}")

    second = run(regk_cmd, env)
    if second.returncode == 0:
        failures.append(
            "SECOND register-knowledge call with identical content_hash+artifact_path "
            f"SUCCEEDED (returncode 0) -- duplicate was NOT rejected. stdout={second.stdout}"
        )
    else:
        try:
            second_result = json.loads(second.stdout)
        except json.JSONDecodeError:
            failures.append(f"second register-knowledge call failed but did not return parseable JSON "
                             f"(looks like an uncaught traceback, not a handled IntegrityError): "
                             f"stdout={second.stdout!r} stderr={second.stderr!r}")
        else:
            if not second_result.get("duplicate"):
                failures.append(f"second register-knowledge call failed but not flagged duplicate=true: {second_result}")
            if "IntegrityError" not in second.stderr and second_result.get("error") != "duplicate_artifact":
                failures.append(f"second register-knowledge call's error was not duplicate_artifact: {second_result}")

    # 4. Claim the same task_key twice.
    claim_cmd = ["python3", SUPERBOSS, "claim-task-key",
                 "--task-key", "dedup-constraint-test-key", "--title", "Dedup constraint test"]
    first_claim = run(claim_cmd, env)
    if first_claim.returncode != 0:
        failures.append(f"first claim-task-key call unexpectedly failed: {first_claim.stdout} {first_claim.stderr}")
    else:
        first_claim_result = json.loads(first_claim.stdout)
        if not first_claim_result.get("claimed"):
            failures.append(f"first claim-task-key call did not report claimed=true: {first_claim_result}")

    second_claim = run(claim_cmd, env)
    try:
        second_claim_result = json.loads(second_claim.stdout)
    except json.JSONDecodeError:
        failures.append(f"second claim-task-key call did not return parseable JSON: "
                         f"stdout={second_claim.stdout!r} stderr={second_claim.stderr!r}")
    else:
        if second_claim_result.get("claimed", True):
            failures.append(
                "SECOND claim-task-key call with identical task_key reported claimed=true "
                f"-- duplicate was NOT rejected: {second_claim_result}"
            )
        if second_claim_result.get("error") != "duplicate_task_key":
            failures.append(f"second claim-task-key call did not report error=duplicate_task_key: {second_claim_result}")

    # 5. Directly confirm the real sqlite3 exception class, not just the CLI
    #    wrapper's interpretation of it -- opens the same temp DB and issues
    #    the same INSERT sqlite3 itself would run, unwrapped.
    import sqlite3
    conn = sqlite3.connect(db_path)
    raised_integrity_error = False
    try:
        conn.execute(
            "INSERT INTO task_claims (claim_id, task_key, ts) VALUES (?,?,?)",
            ("CLM-direct-test", "dedup-constraint-test-key", "2026-07-31T00:00:00Z"),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raised_integrity_error = True
    finally:
        conn.close()
    if not raised_integrity_error:
        failures.append("direct sqlite3 INSERT of a duplicate task_key did NOT raise sqlite3.IntegrityError")

    if failures:
        print("FAIL:\n" + "\n".join(f"  - {f}" for f in failures))
        return 1

    print("PASS: duplicate content_hash+artifact_path rejected; duplicate task_key rejected; "
          "raw sqlite3.IntegrityError confirmed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
