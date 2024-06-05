import json
import sys


in_text = sys.stdin.read()
print(in_text)
parsed = json.loads(in_text.split("\t")[-1])
print(parsed)
assert len(parsed["points"]) == 2
assert {"name": "library_entrypoint.abort", "tags": ["reason:integration"]} in parsed["points"]
assert len([a for a in parsed["points"] if a["name"] == "library_entrypoint.abort.integration"]) == 1
