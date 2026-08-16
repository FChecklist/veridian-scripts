import json

nums = [int(x) for x in open('.scratch/pr_numbers.txt').read().split()]
for n in nums:
    comments = json.load(open(f'.scratch/pr/{n}.comments.json'))
    audits = [c for c in comments if isinstance(c.get('body'), str) and c['body'].strip().startswith('AUDIT:')]
    if not audits:
        print(f"\n##### PR {n}: NO AUDIT COMMENT #####")
        continue
    last = audits[-1]
    body = last['body']
    # print verdict line + Issues found section only, truncate
    print(f"\n##### PR {n} @ {last['created_at']} #####")
    print(body[:900])
