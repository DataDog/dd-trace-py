import json
from pathlib import Path
import sys
import sysconfig
from typing import Any

import jsonschema
from jsonschema.exceptions import ValidationError
import pytest

from ddtrace.internal.datadog.profiling.code_provenance import get_code_provenance_file


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


def _read_json(file_path: str) -> dict[str, Any]:
    with open(file_path, encoding="utf-8") as f:
        result: dict[str, Any] = json.load(f)
        return result


class TestCodeProvenance:
    @pytest.fixture(autouse=True)
    def _reset_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ddtrace.internal.datadog.profiling import code_provenance

        monkeypatch.setattr(code_provenance, "_code_provenance_file_path", None)

    def test_outputs_valid_json(self) -> None:
        # End to end test to ensure that the output is valid JSON
        file_path = get_code_provenance_file()
        assert file_path is not None
        with open(file_path, encoding="utf-8") as f:
            assert is_valid_json(f.read())

    def test_valid_json_but_invalid_schema(self) -> None:
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
    def test_lib_paths_are_absolute(self) -> None:
        file_path = get_code_provenance_file()
        assert file_path is not None
        json_obj = _read_json(file_path)

        site_packages_path = sysconfig.get_path("purelib")
        for item in json_obj["v1"]:
            if item["name"] == "stdlib":
                # See below test_stdlib_paths
                continue
            for path in item["paths"]:
                assert path.startswith("/") and path.startswith(site_packages_path)

    @pytest.mark.skipif(sys.version_info < (3, 10), reason="Python 3.10+ only")
    def test_stdlib_paths(self) -> None:
        file_path = get_code_provenance_file()
        assert file_path is not None
        json_obj = _read_json(file_path)

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
    def test_main_package_my_code(self) -> None:
        import json

        from ddtrace.internal.datadog.profiling.code_provenance import get_code_provenance_file

        file_path = get_code_provenance_file()
        assert file_path is not None
        with open(file_path, encoding="utf-8") as f:
            json_obj = json.load(f)
        # Really what we're checking is that whatever package the user calls the
        # "main" package isn't called a library. Normally this would matter if
        # the user installs their package as a dependency and then runs it as
        # the main package. But for the sake of this test, just call ddtrace the
        # "main" package.
        assert any(info["name"] == "ddtrace" and info["kind"] == "" for info in json_obj["v1"])

    def test_file_path_cached_after_first_call(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from ddtrace.internal.datadog.profiling import code_provenance

        expected_json = json.dumps({"v1": []})
        cache_file = tmp_path / "code-provenance.json"
        cache_file.write_text(expected_json, encoding="utf-8")

        lock_file = tmp_path / "code-provenance.lock"
        monkeypatch.setattr(code_provenance, "_cache_file_and_lock", lambda: (cache_file, lock_file))
        monkeypatch.setattr(code_provenance, "_ensure_cache_file", lambda *_: pytest.fail("must use existing cache"))
        monkeypatch.setattr(code_provenance, "_compute_json_str", lambda: pytest.fail("must use existing cache"))

        # First call resolves the file path
        assert code_provenance.get_code_provenance_file() == str(cache_file)
        # Second call returns cached path even if file is deleted
        cache_file.unlink()
        assert code_provenance.get_code_provenance_file() == str(cache_file)

    def test_file_written_once_then_path_cached(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

        result = code_provenance.get_code_provenance_file()
        assert result == str(cache_file)
        assert calls == 1
        assert cache_file.read_text(encoding="utf-8") == expected_json

        # Second call uses cached path, doesn't recompute
        cache_file.unlink()
        assert code_provenance.get_code_provenance_file() == str(cache_file)
        assert calls == 1

    def test_returns_none_when_lock_is_contended(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from ddtrace.internal.datadog.profiling import code_provenance

        cache_file = tmp_path / "code-provenance.json"
        lock_file = tmp_path / "code-provenance.lock"
        monkeypatch.setattr(code_provenance, "_cache_file_and_lock", lambda: (cache_file, lock_file))
        monkeypatch.setattr(code_provenance, "_ensure_cache_file", lambda *_: False)
        monkeypatch.setattr(
            code_provenance, "_compute_json_str", lambda: pytest.fail("must not compute when lock is contended")
        )

        assert code_provenance.get_code_provenance_file() is None
        assert code_provenance._code_provenance_file_path is None

    def test_retries_after_lock_contention_until_file_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ddtrace.internal.datadog.profiling import code_provenance

        cache_file = tmp_path / "code-provenance.json"
        lock_file = tmp_path / "code-provenance.lock"
        monkeypatch.setattr(code_provenance, "_cache_file_and_lock", lambda: (cache_file, lock_file))

        expected_json = json.dumps({"v1": [{"kind": "library", "name": "foo", "version": "1.2.3", "paths": ["/x"]}]})
        calls = 0

        def _ensure(*_):
            nonlocal calls
            calls += 1
            if calls == 1:
                return False
            # Simulate another process wrote the file
            cache_file.write_text(expected_json, encoding="utf-8")
            return True

        monkeypatch.setattr(code_provenance, "_ensure_cache_file", _ensure)

        # First call: lock contended
        assert code_provenance.get_code_provenance_file() is None
        assert code_provenance._code_provenance_file_path is None
        # Second call: succeeds
        assert code_provenance.get_code_provenance_file() == str(cache_file)
        # Third call: uses cached path
        assert code_provenance.get_code_provenance_file() == str(cache_file)

    def test_cache_key_changes_with_main_package(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ddtrace.internal.datadog.profiling import code_provenance

        monkeypatch.delenv("DD_MAIN_PACKAGE", raising=False)
        key_without_main_package = code_provenance._cache_basename()
        monkeypatch.setenv("DD_MAIN_PACKAGE", "ddtrace")
        key_with_main_package = code_provenance._cache_basename()

        assert key_without_main_package != key_with_main_package

    def test_cache_key_changes_with_sys_path_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Bazel targets can share entries in a different order; since the
        # provenance builder stops at the first match, the cache key must
        # depend on order, not just the set of entries.
        from ddtrace.internal.datadog.profiling import code_provenance

        monkeypatch.setattr(sys, "path", ["/a", "/b", "/c"])
        key_one = code_provenance._cache_basename()
        monkeypatch.setattr(sys, "path", ["/c", "/b", "/a"])
        key_two = code_provenance._cache_basename()

        assert key_one != key_two

    def test_cache_key_changes_with_bazel_runfiles_contents(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A Bazel target rebuilt in place keeps the same sys.path strings while
        # its dependency set changes. The cache key must fold in the runfiles
        # site-packages contents so a rebuild does not serve stale provenance.
        from ddtrace.internal.datadog.profiling import code_provenance

        sp = tmp_path / "dep" / "site-packages"
        sp.mkdir(parents=True)
        (sp / "foo-1.0.dist-info").mkdir()

        monkeypatch.setenv("RUNFILES_DIR", str(tmp_path))
        monkeypatch.setattr(sys, "path", [str(sp)])

        key_before = code_provenance._cache_basename()
        # Simulate a rebuild that swaps in a different dependency version.
        (sp / "foo-1.0.dist-info").rmdir()
        (sp / "foo-2.0.dist-info").mkdir()
        key_after = code_provenance._cache_basename()

        assert key_before != key_after

    def test_cache_key_tolerates_undecodable_sys_path_entry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Non-UTF-8 path bytes surface as surrogate escapes in sys.path; the
        # cache hash must use os.fsencode so it does not raise UnicodeEncodeError
        # and break the profiling upload path.
        from ddtrace.internal.datadog.profiling import code_provenance

        monkeypatch.delenv("RUNFILES_DIR", raising=False)
        monkeypatch.delenv("RUNFILES_MANIFEST_FILE", raising=False)
        monkeypatch.setattr(sys, "path", ["/weird/\udcff/path", "/normal"])

        key = code_provenance._cache_basename()

        assert key.startswith(code_provenance._CODE_PROVENANCE_CACHE_PREFIX)

    def test_bazel_ignores_host_site_packages_outside_runfiles(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A host/virtualenv site-packages can sit on sys.path ahead of the Bazel
        # runfiles dirs. A dependency present in both (e.g. requests) must be
        # recorded from runfiles, not the host path -- the profile frames live
        # under runfiles and would never match a host path.
        from ddtrace.internal.datadog.profiling import code_provenance
        from ddtrace.internal.packages import Distribution

        runfiles_root = tmp_path / "app.runfiles"
        runfiles_sp = runfiles_root / "dep" / "site-packages"
        (runfiles_sp / "requests").mkdir(parents=True)
        (runfiles_sp / "requests" / "__init__.py").write_text("")

        # Host venv site-packages, NOT under the runfiles tree.
        host_sp = tmp_path / "venv" / "site-packages"
        (host_sp / "requests").mkdir(parents=True)
        (host_sp / "requests" / "__init__.py").write_text("")

        monkeypatch.setattr(
            code_provenance,
            "_package_for_root_module_mapping",
            lambda: {"requests": Distribution(name="requests", version="2.0")},
        )
        monkeypatch.setenv("RUNFILES_DIR", str(runfiles_root))
        # Host site-packages deliberately first on sys.path.
        monkeypatch.setattr(sys, "path", [str(host_sp), str(runfiles_sp)])

        cp = code_provenance.CodeProvenance()
        libs = {lib.name: lib for lib in cp.libraries}

        assert "requests" in libs
        assert libs["requests"].paths == {str(runfiles_sp / "requests")}

    def test_bazel_fallback_resolves_single_file_module(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # In Bazel runfiles a single-file dependency such as six.py lives in an
        # isolated per-package dir on sys.path, not in purelib. The fallback
        # must locate it there instead of recording a non-existent purelib path.
        from ddtrace.internal.datadog.profiling import code_provenance
        from ddtrace.internal.packages import Distribution

        bazel_dir = tmp_path / "runfiles" / "uniqbazelmod" / "site-packages"
        bazel_dir.mkdir(parents=True)
        module_file = bazel_dir / "uniqbazelmod.py"
        module_file.write_text("")

        monkeypatch.setattr(
            code_provenance,
            "_package_for_root_module_mapping",
            lambda: {"uniqbazelmod.py": Distribution(name="uniqbazelmod", version="1.0")},
        )
        monkeypatch.setenv("RUNFILES_DIR", str(tmp_path))
        monkeypatch.setattr(sys, "path", [str(bazel_dir)])

        cp = code_provenance.CodeProvenance()
        libs = {lib.name: lib for lib in cp.libraries}

        assert "uniqbazelmod" in libs
        assert libs["uniqbazelmod"].paths == {str(module_file)}

    def test_bazel_prefers_runfiles_over_shadowing_purelib(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # When a dependency exists both in the host purelib and in the Bazel
        # runfiles (the shadowed-dependency case this feature targets), profiles
        # contain the runfiles path, so the recorded path must be the runfiles
        # one -- not the host purelib path that would never match the frames.
        import sysconfig as _sysconfig

        from ddtrace.internal.datadog.profiling import code_provenance
        from ddtrace.internal.packages import Distribution

        fake_purelib = tmp_path / "purelib"
        (fake_purelib / "shadowedmod").mkdir(parents=True)
        (fake_purelib / "shadowedmod" / "__init__.py").write_text("")

        runfiles_dir = tmp_path / "runfiles" / "shadowedmod" / "site-packages"
        (runfiles_dir / "shadowedmod").mkdir(parents=True)
        (runfiles_dir / "shadowedmod" / "__init__.py").write_text("")

        real_get_path = _sysconfig.get_path

        def _get_path(name: str, *args: Any, **kwargs: Any) -> str:
            if name == "purelib":
                return str(fake_purelib)
            return real_get_path(name, *args, **kwargs)

        monkeypatch.setattr(code_provenance.sysconfig, "get_path", _get_path)
        monkeypatch.setattr(
            code_provenance,
            "_package_for_root_module_mapping",
            lambda: {"shadowedmod": Distribution(name="shadowedmod", version="1.0")},
        )
        monkeypatch.setenv("RUNFILES_DIR", str(tmp_path))
        monkeypatch.setattr(sys, "path", [str(runfiles_dir)])

        cp = code_provenance.CodeProvenance()
        libs = {lib.name: lib for lib in cp.libraries}

        assert "shadowedmod" in libs
        assert libs["shadowedmod"].paths == {str(runfiles_dir / "shadowedmod")}

    def test_bazel_ignores_non_site_packages_sys_path_entries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Bazel sys.path includes workspace/source roots, often ahead of the
        # dependency site-packages dirs. A user-code package that merely shares
        # a name with an installed dist must not get its source-root path
        # recorded as a library path (that would misclassify "my code" frames).
        # Only dependency ".../site-packages/<pkg>" dirs should be searched.
        from ddtrace.internal.datadog.profiling import code_provenance
        from ddtrace.internal.packages import Distribution

        # A workspace/source root (NOT site-packages) holding same-named code.
        source_root = tmp_path / "workspace"
        (source_root / "depmod").mkdir(parents=True)
        (source_root / "depmod" / "__init__.py").write_text("")

        # The actual dependency lives in an isolated runfiles site-packages dir.
        runfiles_dir = tmp_path / "runfiles" / "depmod" / "site-packages"
        (runfiles_dir / "depmod").mkdir(parents=True)
        (runfiles_dir / "depmod" / "__init__.py").write_text("")

        monkeypatch.setattr(
            code_provenance,
            "_package_for_root_module_mapping",
            lambda: {"depmod": Distribution(name="depmod", version="1.0")},
        )
        monkeypatch.setenv("RUNFILES_DIR", str(tmp_path))
        # Source root deliberately ahead of the dependency site-packages dir.
        monkeypatch.setattr(sys, "path", [str(source_root), str(runfiles_dir)])

        cp = code_provenance.CodeProvenance()
        libs = {lib.name: lib for lib in cp.libraries}

        assert "depmod" in libs
        assert libs["depmod"].paths == {str(runfiles_dir / "depmod")}

    def test_bazel_manifest_mode_resolves_dependency_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Manifest mode: only RUNFILES_MANIFEST_FILE is set (no RUNFILES_DIR),
        # and the dependency site-packages can live outside any *.runfiles tree.
        # The dependency path must still be recorded from sys.path (resolved via
        # the manifest) rather than falling back to the host purelib, which
        # would leave the profile frames classified as user code.
        from ddtrace.internal import packages
        from ddtrace.internal.datadog.profiling import code_provenance
        from ddtrace.internal.packages import Distribution

        dep_sp = tmp_path / "execroot" / "dep" / "site-packages"
        (dep_sp / "manifestmod").mkdir(parents=True)
        (dep_sp / "manifestmod" / "__init__.py").write_text("")

        manifest = tmp_path / "app.runfiles_manifest"
        manifest.write_text(f"app/manifestmod/__init__.py {dep_sp / 'manifestmod' / '__init__.py'}\n")

        # Clear the cached manifest parse so this test's manifest is read.
        inner = getattr(packages._bazel_manifest_site_packages, "__wrapped__", None)
        if inner is not None and hasattr(inner, "__callonce_result__"):
            del inner.__callonce_result__

        monkeypatch.setattr(
            code_provenance,
            "_package_for_root_module_mapping",
            lambda: {"manifestmod": Distribution(name="manifestmod", version="1.0")},
        )
        monkeypatch.delenv("RUNFILES_DIR", raising=False)
        monkeypatch.setenv("RUNFILES_MANIFEST_FILE", str(manifest))
        monkeypatch.setattr(sys, "path", [str(dep_sp)])

        try:
            cp = code_provenance.CodeProvenance()
            libs = {lib.name: lib for lib in cp.libraries}

            assert "manifestmod" in libs
            assert libs["manifestmod"].paths == {str(dep_sp / "manifestmod")}
        finally:
            if inner is not None and hasattr(inner, "__callonce_result__"):
                del inner.__callonce_result__

    def test_cache_basename_distinguishes_cwd_for_relative_sys_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # When sys.path holds '' (Python's default cwd entry) or other relative
        # entries, two processes that differ only by cwd can still import
        # different cwd-local packages. The cache basename must therefore differ
        # between cwds, or the second process serves a stale code-provenance.json.
        from ddtrace.internal.datadog.profiling import code_provenance

        monkeypatch.setattr(sys, "path", [""])

        cwd_a = tmp_path / "a"
        cwd_b = tmp_path / "b"
        cwd_a.mkdir()
        cwd_b.mkdir()

        monkeypatch.chdir(cwd_a)
        basename_a = code_provenance._cache_basename()
        monkeypatch.chdir(cwd_b)
        basename_b = code_provenance._cache_basename()

        assert basename_a != basename_b
