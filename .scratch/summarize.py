import json

nums = [int(x) for x in open('.scratch/pr_numbers.txt').read().split()]
for n in nums:
    pr = json.load(open(f'.scratch/pr/{n}.pr.json'))
    comments = json.load(open(f'.scratch/pr/{n}.comments.json'))
    head_sha = pr.get('head', {}).get('sha', '?')[:10]
    mergeable = pr.get('mergeable')
    mergeable_state = pr.get('mergeable_state')
    updated_at = pr.get('updated_at')
    # find last AUDIT: PASS/FAIL comment
    audits = [c for c in comments if isinstance(c.get('body'), str) and c['body'].strip().startswith('AUDIT:')]
    last_audit = audits[-1] if audits else None
    verdict = None
    audit_at = None
    if last_audit:
        verdict = last_audit['body'].strip().split('\n')[0]
        audit_at = last_audit['created_at']
    print(f"{n}\thead={head_sha}\tmergeable={mergeable}/{mergeable_state}\tPR_updated={updated_at}\tlast_audit=[{verdict}]@{audit_at}\tn_audits={len(audits)}")
