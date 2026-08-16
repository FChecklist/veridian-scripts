import json, subprocess

out = subprocess.run(
    ["gh", "pr", "list", "--repo", "FChecklist/veridian-scripts", "--state", "open",
     "--limit", "200", "--json", "number,title,mergeable,createdAt,updatedAt"],
    capture_output=True, text=True, check=True
).stdout

data = json.loads(out)
print("total open:", len(data))
conflicting = [d for d in data if d["mergeable"] == "CONFLICTING"]
unknown = [d for d in data if d["mergeable"] == "UNKNOWN"]
mergeable = [d for d in data if d["mergeable"] == "MERGEABLE"]
print("conflicting:", len(conflicting))
print("unknown:", len(unknown))
print("mergeable:", len(mergeable))
print()
print("=== CONFLICTING ===")
for d in sorted(conflicting, key=lambda x: x["number"]):
    print(d["number"], d["createdAt"], d["title"][:70])
print()
print("=== UNKNOWN (need re-check) ===")
for d in sorted(unknown, key=lambda x: x["number"]):
    print(d["number"], d["createdAt"], d["title"][:70])
