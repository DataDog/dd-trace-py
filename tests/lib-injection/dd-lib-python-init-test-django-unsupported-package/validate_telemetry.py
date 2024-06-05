import json
import sys


parsed = json.loads(sys.stdin.read())

print(parsed)
assert len(parsed["points"]) == 2
assert {"name": "library_endpoint.abort", "tags": ["reason:integration"]} in parsed["points"]
assert len([a for a in parsed["points"] if a["name"] == "library_endpoint.abort.integration"]) == 1
