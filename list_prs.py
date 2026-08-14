import json
with open('prs.json') as f:
    data = f.read()
prs = json.loads(data, strict=False)
print(len(prs))
for p in prs:
    print(p['number'], p['mergeable'], p['mergeStateStatus'], p['headRefName'])
