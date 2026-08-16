import subprocess, sys

PAIR = [
    (419, "worker/task-20260815-144312-reconcile-live-deploy-drift---opt-veridi"),
    (429, "worker/task-20260815-231659-commit---fix---wire-in-the-uncommitted-q"),
]

def run(*args, check=True):
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        print("CMD FAILED:", args); print(r.stdout); print(r.stderr); sys.exit(1)
    return r

for num, head in PAIR:
    head_sha = run("git", "rev-parse", f"origin/{head}").stdout.strip()
    print(f"--- merging PR #{num} ({head} @ {head_sha[:10]}) ---")
    msg = f"Merge PR #{num} ({head}) into rebase-and-land bundle 2\n\nOriginal: FChecklist/veridian-scripts#{num}\nHead SHA: {head_sha}"
    mr = run("git", "merge", "--no-ff", "-m", msg, head_sha, check=False)
    if mr.returncode != 0:
        status = run("git", "status", "--porcelain").stdout
        conflicted = [l[3:] for l in status.splitlines() if l.startswith("UU") or l.startswith("AA")]
        print("conflicted files:", conflicted)
        if conflicted == ["PROGRESS.md"]:
            run("git", "checkout", "--ours", "PROGRESS.md")
            run("git", "add", "PROGRESS.md")
            run("git", "commit", "--no-edit")
            print(f"OK merged #{num} (PROGRESS.md conflict resolved)")
        else:
            print(f"UNEXPECTED conflicts for #{num}: {conflicted}")
            print(run("git", "diff").stdout[:8000])
            sys.exit(2)
    else:
        print(f"OK merged #{num} clean")

print(run("git", "log", "--oneline", "-5").stdout)
print(run("git", "diff", "--stat", "origin/main", "HEAD").stdout)
