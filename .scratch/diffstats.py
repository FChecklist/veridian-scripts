import json, subprocess

trivial = [78, 266, 331, 332, 370, 410, 412, 415, 419, 428, 429, 430]
with open(".scratch/triage_results2.json") as f:
    results = {r["num"]: r for r in json.load(f) if r.get("status") == "ok"}

file_owner = {}
for num in trivial:
    r = results[num]
    out = subprocess.run(["git", "diff", "--stat", r["base_sha"], r["head_sha"]],
                          capture_output=True, text=True).stdout
    files = []
    for line in out.splitlines()[:-1]:
        fname = line.split("|")[0].strip()
        if fname:
            files.append(fname)
    print(num, files)
    for fn in files:
        if fn == "PROGRESS.md":
            continue
        file_owner.setdefault(fn, []).append(num)

print("\n=== overlaps ===")
for fn, nums in file_owner.items():
    if len(nums) > 1:
        print(fn, nums)
