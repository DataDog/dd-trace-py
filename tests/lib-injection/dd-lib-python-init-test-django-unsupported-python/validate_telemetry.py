import json
import sys


in_text = sys.stdin.read()
print(in_text)
parsed = json.loads(in_text.split("\t")[-1])
print(parsed)
assert len(parsed["points"]) == 2
assert {"name": "library_entrypoint.abort", "tags": ["reason:incompatible_runtime"]} in parsed["points"]
assert {"name": "library_entrypoint.abort.runtime", "tags": []} in parsed["points"]
