import json
import sys


parsed = json.loads(sys.stdin.read())

print(parsed)
assert len(parsed["points"]) == 2
assert {"name": "library_entrypoint.abort", "tags": ["reason:incompatible_runtime"]} in parsed["points"]
assert {"name": "library_entrypoint.abort.runtime", "tags": []} in parsed["points"]
