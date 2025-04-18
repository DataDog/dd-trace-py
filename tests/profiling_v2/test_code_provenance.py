import json
import sys
import sysconfig

import pytest

from ddtrace.internal.datadog.profiling.code_provenance import json_str_to_export


PY_VERSION = sys.version_info[:2]

# importing jsonschema in 3.8 results in an error
#     raise exceptions.NoSuchResource(ref=uri) from None
# E   referencing.exceptions.NoSuchResource: 'http://json-schema.org/draft-03/schema#'
if PY_VERSION > (3, 8):
    import jsonschema  # noqa: E402


# Copied from RFC
SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema",
    "type": "object",
    "required": ["v1"],
    "properties": {
        "v1": {
            "type": "array",
            "additionalItems": True,
            "items": {
                "anyOf": [
                    {
                        "type": "object",
                        "required": ["kind", "name", "version", "paths"],
                        "properties": {
                            "kind": {"type": "string"},
                            "name": {"type": "string"},
                            "version": {"type": "string"},
                            "paths": {
                                "type": "array",
                                "additionalItems": True,
                                "items": {"anyOf": [{"type": "string"}]},
                            },
                        },
                        "additionalProperties": True,
                    }
                ]
            },
        }
    },
    "additionalProperties": True,
}


def is_valid_json(s: str) -> bool:
    try:
        json_obj = json.loads(s)
        if PY_VERSION > (3, 8):
            try:
                jsonschema.validate(json_obj, SCHEMA)
            except jsonschema.exceptions.ValidationError as e:
                print(f"Validation error: {e.message}")
                return False
        return True
    except json.JSONDecodeError:
        print(f"Invalid JSON: {s}")
        return False


class TestCodeProvenance:
    def test_outputs_valid_json(self):
        # End to end test to ensure that the output is valid JSON
        json_str = json_str_to_export()
        assert is_valid_json(json_str)

    @pytest.mark.skipif(PY_VERSION == (3, 8), reason="jsonschema results in an error in 3.8 with ddtrace")
    def test_valid_json_but_invalid_schema(self):
        # Just a sanity check to ensure that jsonschema is working as expected
        json_obj = {
            "v1": [
                {
                    "kind": "test",
                    "name": "test",
                    "version": 1,
                    "paths": ["test"],
                }
            ]
        }
        assert not is_valid_json(json.dumps(json_obj))

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix only")
    def test_lib_paths_are_absolute(self):
        json_str = json_str_to_export()
        json_obj = json.loads(json_str)

        site_packages_path = sysconfig.get_path("purelib")
        for item in json_obj["v1"]:
            if item["name"] == "stdlib":
                # See below test_stdlib_paths
                continue
            for path in item["paths"]:
                assert path.startswith("/") and path.startswith(site_packages_path)

    @pytest.mark.skipif(sys.version_info < (3, 10), reason="Python 3.10+ only")
    def test_stdlib_paths(self):
        json_str = json_str_to_export()
        json_obj = json.loads(json_str)

        # Check that the obj has stdlib
        stdlib = [item for item in json_obj["v1"] if item["name"] == "stdlib"]
        assert len(stdlib) == 1
        stdlib_item = stdlib[0]
        stdlib_paths = stdlib_item["paths"]

        # We add stdlib and three frozen modules, and expect to have more frozen
        # modules in the stdlib
        assert len(stdlib_paths) > 4

        for path in stdlib_paths:
            assert path.startswith("<frozen") or path == sysconfig.get_path("stdlib")
