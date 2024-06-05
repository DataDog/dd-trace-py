import json
import sys


in = sys.stdin.read()
print(in)
parsed = json.loads(in)
print(parsed)
assert len(parsed["points"]) == 1
assert parsed["points"][0] == {"name": "library_entrypoint.complete", "tags": ["injection_forced:false"]}
