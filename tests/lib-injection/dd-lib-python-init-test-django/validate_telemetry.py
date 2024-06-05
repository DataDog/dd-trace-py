import json
import sys


parsed = json.loads(sys.stdin.read())

print(parsed)
assert len(parsed["points"]) == 1
assert parsed["points"][0] == {"name": "library_endpoint.complete", "tags": ["injection_forced:false"]}
