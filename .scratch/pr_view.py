import json, subprocess, sys

num = sys.argv[1]
out = subprocess.run(
    ["gh", "pr", "view", num, "--repo", "FChecklist/veridian-scripts",
     "--json", "number,title,mergeable,mergeStateStatus,baseRefName,headRefName,commits,comments,body"],
    capture_output=True, text=True, check=True
).stdout
d = json.loads(out)
print("title:", d["title"])
print("mergeable:", d["mergeable"], "state:", d["mergeStateStatus"])
print("base:", d["baseRefName"], "head:", d["headRefName"])
print("n_commits:", len(d["commits"]))
print("n_comments:", len(d["comments"]))
for c in d["comments"][-6:]:
    print("---COMMENT by", c["author"]["login"], c["createdAt"])
    print(c["body"][:800])
