import json, subprocess, sys

REPO = "FChecklist/veridian-scripts"

def run(*args, check=True):
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{args}\n{r.stderr}")
    return r.stdout.strip()

nums = [int(x) for x in sys.argv[1:]]
run("git", "fetch", "origin", "main", check=False)
main_sha = run("git", "rev-parse", "origin/main")
for num in nums:
    out = subprocess.run(["gh", "pr", "view", str(num), "--repo", REPO, "--json", "headRefName"],
                          capture_output=True, text=True).stdout
    head = json.loads(out)["headRefName"]
    run("git", "fetch", "origin", f"refs/heads/{head}:refs/remotes/origin/{head}", check=False)
    head_sha = run("git", "rev-parse", f"origin/{head}")
    base_sha = run("git", "merge-base", "origin/main", f"origin/{head}")
    mt = subprocess.run(["git", "merge-tree", "--write-tree", "--merge-base", base_sha, main_sha, head_sha],
                         capture_output=True, text=True)
    conflicted = [l for l in mt.stdout.splitlines() if l.startswith("CONFLICT")]
    print(num, "head=", head, head_sha[:10], "conflicts=", conflicted)
