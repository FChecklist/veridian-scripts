import json
d = json.load(open('.scratch/workflows.json'))
print('total_count', d.get('total_count'))
for w in d.get('workflows', []):
    print(w['id'], w['name'], w['path'], w['state'])
