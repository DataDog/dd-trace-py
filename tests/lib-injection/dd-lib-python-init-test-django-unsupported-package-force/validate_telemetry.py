import json
import sys


parsed = json.loads(sys.stdin)

print(parsed)
assert parsed["points"]
