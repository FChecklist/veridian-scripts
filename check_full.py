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

candidates = [385, 384, 371, 247, 244, 233, 232, 205, 200, 198, 118, 99, 93, 90, 71, 61, 60]
for n in candidates:
    s = state[n]
    cs = comments.get(n, [])
    last = cs[-1] if cs else None
    print("="*100)
    print(f"PR #{n} head_sha={s['head_sha']}")
    if last:
        body = last['body']
        has_sha_short = s['head_sha'][:7] in body
        has_sha_full = s['head_sha'] in body
        print(f"  last comment created_at={last['created_at']} sha_in_body(short7)={has_sha_short} sha_in_body(full)={has_sha_full}")
        print(f"  BODY:\n{body}")
    print()
