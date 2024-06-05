import json
import sys


parsed = json.loads(sys.stdin.read())

print(parsed)
assert parsed["points"]
