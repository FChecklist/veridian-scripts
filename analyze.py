import json

state = {}
with open('pr_state.jsonl') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        state[d['number']] = d

comments = {}
with open('pr_comments.jsonl') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        comments[d['number']] = d['audit_comments']

for n in sorted(state.keys(), reverse=True):
    s = state[n]
    cs = comments.get(n, [])
    print("="*100)
    print(f"PR #{n}  mergeable={s['mergeable']} state={s['mergeable_state']} head_sha={s['head_sha'][:12]} updated={s['updated_at']}")
    print(f"  title: {s['title']}")
    print(f"  audit comments: {len(cs)}")
    for c in cs[-2:]:
        body = c['body'].replace('\n', ' ')[:300]
        print(f"    [{c['created_at']}] {c['user']}: {body}")
