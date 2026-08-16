import json, subprocess

with open(".scratch/triage_results.json") as f:
    results = json.load(f)

def run(*args):
    r = subprocess.run(args, capture_output=True, text=True)
    return r.stdout, r.stderr, r.returncode

for r in results:
    if r.get("status") != "ok":
        continue
    head_sha = r["head_sha"]
    main_sha = r["main_sha"]
    base_sha = r["base_sha"]
    out, err, rc = run("git", "merge-tree", "--write-tree", "--merge-base", base_sha, main_sha, head_sha)
    conflicted = []
    for line in out.splitlines():
        if line.startswith("CONFLICT"):
            conflicted.append(line)
    r["conflict_lines"] = conflicted
    r["conflict_files_only_progress"] = (len(conflicted) == 1 and "PROGRESS.md" in conflicted[0])
    print(r["num"], "conflicts:", len(conflicted), "-", conflicted)

with open(".scratch/triage_results2.json", "w") as f:
    json.dump(results, f, indent=2)
