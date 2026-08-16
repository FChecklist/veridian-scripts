import json, subprocess, sys

REPO = "FChecklist/veridian-scripts"
NUMS = [8,61,65,72,78,79,198,204,266,273,276,331,332,355,357,370,405,410,412,
        415,416,417,419,422,423,424,428,429,430,435]

def run(*args, check=True):
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd failed: {args}\n{r.stderr}")
    return r.stdout.strip(), r.returncode

def gh_pr_view(num):
    out, rc = run("gh", "pr", "view", str(num), "--repo", REPO,
                   "--json", "number,title,mergeable,mergeStateStatus,headRefName,baseRefName,isDraft,state", check=False)
    if rc != 0:
        return None
    return json.loads(out)

results = []
for num in NUMS:
    d = gh_pr_view(num)
    if d is None or d.get("state") != "OPEN":
        results.append({"num": num, "status": "not_open_or_missing"})
        continue
    head = d["headRefName"]
    # fetch the head branch
    _, rc = run("git", "fetch", "origin", f"refs/heads/{head}:refs/remotes/origin/{head}", check=False)
    if rc != 0:
        results.append({"num": num, "status": "fetch_failed", "head": head})
        continue
    head_sha, _ = run("git", "rev-parse", f"origin/{head}")
    main_sha, _ = run("git", "rev-parse", "origin/main")
    base_sha, rc = run("git", "merge-base", "origin/main", f"origin/{head}", check=False)
    if rc != 0:
        results.append({"num": num, "status": "no_merge_base", "head": head})
        continue
    # try a real 3-way merge-tree
    mt = subprocess.run(["git", "merge-tree", "--write-tree", "--merge-base", base_sha,
                          main_sha, head_sha], capture_output=True, text=True)
    conflict = mt.returncode != 0
    merged_tree = mt.stdout.strip().split("\n")[0] if mt.stdout.strip() else None
    main_tree, _ = run("git", "rev-parse", f"{main_sha}^{{tree}}")
    superseded = (not conflict) and merged_tree == main_tree
    results.append({
        "num": num, "status": "ok", "head": head, "mergeable": d.get("mergeable"),
        "head_sha": head_sha, "main_sha": main_sha, "base_sha": base_sha,
        "conflict_in_auto_merge": conflict, "superseded_noop": superseded,
        "title": d.get("title"),
    })
    print(num, "mergeable=", d.get("mergeable"), "conflict=",conflict, "superseded=", superseded)

with open(".scratch/triage_results.json", "w") as f:
    json.dump(results, f, indent=2)
