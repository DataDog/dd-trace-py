import json

from ddtrace.internal.datadog.profiling.code_provenance import json_str_to_export


def is_valid_json(s: str) -> bool:
    try:
        json.loads(s)
        return True
    except json.JSONDecodeError:
        return False


class TestCodeProvenance:
    def test_outputs_valid_json(self):
        # End to end test to ensure that the output is valid JSON
        json_str = json_str_to_export()
        assert is_valid_json(json_str)
