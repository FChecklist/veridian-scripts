import json, sys

nums = [int(x) for x in sys.argv[1:]]
for n in nums:
    comments = json.load(open(f'.scratch/pr/{n}.comments.json'))
    audits = [c for c in comments if isinstance(c.get('body'), str) and c['body'].strip().startswith('AUDIT:')]
    last = audits[-1]
    body = last['body']
    # print just the "Issues found:" section, or last part of Evidence Recorded
    idx = body.find('Issues found:')
    if idx == -1:
        idx = body.find('Evidence Recorded:')
    print(f"\n##### PR {n} #####")
    print(body[idx:idx+1200] if idx != -1 else body[-1200:])
