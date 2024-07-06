import json
import sys


in_text = sys.stdin.read()
print(in_text)
parsed = json.loads(in_text.split("\t")[-1])
print(parsed)
assert len(parsed["points"]) == 1
assert parsed["points"][0] == {"name": "library_entrypoint.complete", "tags": ["injection_forced:false"]}
