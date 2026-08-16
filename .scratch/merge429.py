import subprocess, sys

def run(*args, check=True):
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        print("CMD FAILED:", args); print(r.stdout); print(r.stderr); sys.exit(1)
    return r

sha = "e3bde1a2576efe1c718779c47ffc54c741b4a7b9"
msg = ("Merge PR #429 (worker/task-20260815-231659-commit---fix---wire-in-the-uncommitted-q) "
       "into rebase-and-land bundle 2\n\nOriginal: FChecklist/veridian-scripts#429\n"
       f"Head SHA: {sha}")
mr = run("git", "merge", "--no-ff", "-m", msg, sha, check=False)
print("merge rc:", mr.returncode)
status = run("git", "status", "--porcelain").stdout
print(status)
