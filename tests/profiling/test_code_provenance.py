import json
import sys
import sysconfig

import jsonschema
from jsonschema.exceptions import ValidationError
import pytest

from ddtrace.internal.datadog.profiling.code_provenance import json_str_to_export


PY_VERSION = sys.version_info[:2]


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
        try:
            jsonschema.validate(json_obj, SCHEMA)
        except ValidationError as e:
            print(f"Validation error: {e.message}")
            return False
        return True
    except json.JSONDecodeError:
        print(f"Invalid JSON: {s}")
        return False


class TestCodeProvenance:
    @pytest.fixture(autouse=True)
    def _reset_in_memory_cache(self, monkeypatch):
        from ddtrace.internal.datadog.profiling import code_provenance

        monkeypatch.setattr(code_provenance, "_in_memory_code_provenance_json", None)

    def test_outputs_valid_json(self):
        # End to end test to ensure that the output is valid JSON
        json_str = json_str_to_export()
        assert is_valid_json(json_str)

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

    @pytest.mark.subprocess(
        env=dict(DD_MAIN_PACKAGE="ddtrace"),
    )
    def test_main_package_my_code(self):
        import json

        from ddtrace.internal.datadog.profiling.code_provenance import json_str_to_export

        json_str = json_str_to_export()
        json_obj = json.loads(json_str)
        # Really what we're checking is that whatever package the user calls the
        # "main" package isn't called a library. Normally this would matter if
        # the user installs their package as a dependency and then runs it as
        # the main package. But for the sake of this test, just call ddtrace the
        # "main" package.
        assert any(info["name"] == "ddtrace" and info["kind"] == "" for info in json_obj["v1"])

    def test_json_from_file_cache_then_in_memory(self, tmp_path, monkeypatch):
        from ddtrace.internal.datadog.profiling import code_provenance

        expected_json = json.dumps({"v1": []})
        cache_file = tmp_path / "code-provenance.json"
        cache_file.write_text(expected_json, encoding="utf-8")

        lock_file = tmp_path / "code-provenance.lock"
        monkeypatch.setattr(code_provenance, "_cache_file_and_lock", lambda: (cache_file, lock_file))
        monkeypatch.setattr(
            code_provenance, "_read_or_compute_with_nonblocking_lock", lambda *_: pytest.fail("must use cache")
        )
        monkeypatch.setattr(code_provenance, "_compute_json_str", lambda: pytest.fail("must use cache"))

        assert code_provenance.json_str_to_export() == expected_json
        cache_file.unlink()
        assert code_provenance.json_str_to_export() == expected_json

    def test_json_cached_in_file_once_then_in_memory(self, tmp_path, monkeypatch):
        from ddtrace.internal.datadog.profiling import code_provenance

        cache_file = tmp_path / "code-provenance.json"
        lock_file = tmp_path / "code-provenance.lock"
        monkeypatch.setattr(code_provenance, "_cache_file_and_lock", lambda: (cache_file, lock_file))

        calls = 0
        expected_json = json.dumps({"v1": [{"kind": "library", "name": "foo", "version": "1.2.3", "paths": ["/x"]}]})

        def _compute_json():
            nonlocal calls
            calls += 1
            return expected_json

        monkeypatch.setattr(code_provenance, "_compute_json_str", _compute_json)

        assert code_provenance.json_str_to_export() == expected_json
        assert calls == 1
        assert cache_file.read_text(encoding="utf-8") == expected_json

        cache_file.unlink()
        assert code_provenance.json_str_to_export() == expected_json
        assert calls == 1

    def test_returns_empty_string_when_lock_is_contended(self, tmp_path, monkeypatch):
        from ddtrace.internal.datadog.profiling import code_provenance

        cache_file = tmp_path / "code-provenance.json"
        lock_file = tmp_path / "code-provenance.lock"
        monkeypatch.setattr(code_provenance, "_cache_file_and_lock", lambda: (cache_file, lock_file))
        monkeypatch.setattr(code_provenance, "_read_or_compute_with_nonblocking_lock", lambda *_: "")
        monkeypatch.setattr(
            code_provenance, "_compute_json_str", lambda: pytest.fail("must not compute when lock is contended")
        )

        assert code_provenance.json_str_to_export() == ""
        assert code_provenance._in_memory_code_provenance_json is None

    def test_retries_after_lock_contention_until_non_empty(self, tmp_path, monkeypatch):
        from ddtrace.internal.datadog.profiling import code_provenance

        cache_file = tmp_path / "code-provenance.json"
        lock_file = tmp_path / "code-provenance.lock"
        monkeypatch.setattr(code_provenance, "_cache_file_and_lock", lambda: (cache_file, lock_file))

        expected_json = json.dumps({"v1": [{"kind": "library", "name": "foo", "version": "1.2.3", "paths": ["/x"]}]})
        calls = 0

        def _read_or_compute(*_):
            nonlocal calls
            calls += 1
            return "" if calls == 1 else expected_json

        monkeypatch.setattr(code_provenance, "_read_or_compute_with_nonblocking_lock", _read_or_compute)

        assert code_provenance.json_str_to_export() == ""
        assert code_provenance._in_memory_code_provenance_json is None
        assert code_provenance.json_str_to_export() == expected_json
        assert code_provenance.json_str_to_export() == expected_json

    def test_cache_key_changes_with_main_package(self, monkeypatch):
        from ddtrace.internal.datadog.profiling import code_provenance

        monkeypatch.delenv("DD_MAIN_PACKAGE", raising=False)
        key_without_main_package = code_provenance._cache_basename()
        monkeypatch.setenv("DD_MAIN_PACKAGE", "ddtrace")
        key_with_main_package = code_provenance._cache_basename()

        assert key_without_main_package != key_with_main_package
