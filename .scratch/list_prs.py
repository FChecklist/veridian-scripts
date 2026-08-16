import json
data = json.load(open('.scratch/prs.json'))
print('COUNT', len(data))
for d in sorted(data, key=lambda x: x['number'], reverse=True):
    print(d['number'], d['mergeable'], d['headRefName'], '|', d['title'][:70])
