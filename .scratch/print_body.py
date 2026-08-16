import json, sys
d = json.load(open(sys.argv[1]))
print(d.get('body') or '')
