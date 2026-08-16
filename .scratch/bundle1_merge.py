import json, subprocess, sys

BUNDLE1 = [78, 266, 331, 332, 370, 410, 412, 415, 428, 430]

with open(".scratch/triage_results2.json") as f:
    results = {r["num"]: r for r in json.load(f) if r.get("status") == "ok"}

def run(*args, check=True, cwd=None):
    r = subprocess.run(args, capture_output=True, text=True, cwd=cwd)
    if check and r.returncode != 0:
        print("CMD FAILED:", args)
        print("STDOUT:", r.stdout)
        print("STDERR:", r.stderr)
        sys.exit(1)
    return r

for num in BUNDLE1:
    r = results[num]
    head = r["head"]
    head_sha = r["head_sha"]
    print(f"--- merging PR #{num} ({head} @ {head_sha[:10]}) ---")
    mr = run("git", "merge", "--no-ff", "-m",
             f"Merge PR #{num} ({head}) into rebase-and-land bundle\n\nOriginal: FChecklist/veridian-scripts#{num}\nHead SHA: {head_sha}",
             head_sha, check=False)
    if mr.returncode != 0:
        # Expect only PROGRESS.md to conflict
        status = run("git", "status", "--porcelain").stdout
        conflicted = [l[3:] for l in status.splitlines() if l.startswith("UU") or l.startswith("AA")]
        print("conflicted files:", conflicted)
        if conflicted != ["PROGRESS.md"]:
            print(f"UNEXPECTED CONFLICT SET for PR #{num}: {conflicted} -- aborting merge, needs manual handling")
            run("git", "merge", "--abort", check=False)
            print(f"SKIPPED #{num}")
            continue
        run("git", "checkout", "--ours", "PROGRESS.md")
        run("git", "add", "PROGRESS.md")
        run("git", "commit", "--no-edit")
    print(f"OK merged #{num}")

print("DONE. HEAD is now:")
print(run("git", "log", "--oneline", "-15").stdout)
