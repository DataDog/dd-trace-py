#!/usr/bin/env python3
from ast import unparse
import logging
import os
from pathlib import Path
from textwrap import dedent
from unittest import mock

import pytest

from ddtrace.appsec._iast._ast import iastpatch
from ddtrace.appsec._iast._ast.ast_patching import astpatch_module
from ddtrace.appsec._iast._ast.ast_patching import visit_ast
from ddtrace.appsec._iast._constants import IAST
from ddtrace.internal.utils.formats import asbool
from tests.utils import override_env


_PREFIX = IAST.PATCH_ADDED_SYMBOL_PREFIX


@pytest.mark.parametrize(
    "source_text, module_path, module_name",
    [
        ("print('hi')", "test.py", "test"),
        ("print('str')", "test.py", "test"),
        ("str", "test.py", "test"),
    ],
)
def test_visit_ast_unchanged(source_text, module_path, module_name):
    """
    Source texts not containing:
    - str() calls
    - [...]  // To be filled with more aspects
    won't be modified by ast patching, so will return empty string
    """
    assert visit_ast(source_text, module_path, module_name) is None


@pytest.mark.parametrize(
    "source_text, module_path, module_name",
    [
        ("print(str('hi'))", "test.py", "test"),
        ("print(str('hi' + 'bye'))", "test.py", "test"),
        ("print('hi' + 'bye')", "test.py", "test"),
    ],
)
def test_visit_ast_changed(source_text, module_path, module_name):
    """
    Source texts containing:
    - str() calls
    - [...]  // To be filled with more aspects
    will be modified by ast patching, so will not return empty string
    """
    assert visit_ast(source_text, module_path, module_name) is not None


@pytest.mark.parametrize(
    "module_name",
    [
        ("tests.appsec.iast.fixtures.ast.str.class_str"),
        ("tests.appsec.iast.fixtures.ast.str.function_str"),
        ("tests.appsec.iast.fixtures.ast.str.non_utf8_content"),  # EUC-JP file content
    ],
)
def test_astpatch_module_changed(module_name):
    module_path, new_ast = astpatch_module(__import__(module_name, fromlist=["*"]))
    assert ("", None) != (module_path, new_ast)
    new_code = unparse(new_ast)
    assert new_code.startswith(
        f"import ddtrace.appsec._iast.sources as {_PREFIX}sources"
        f"\nimport ddtrace.appsec._iast.taint_sinks as {_PREFIX}taint_sinks"
        f"\nimport ddtrace.appsec._iast._taint_tracking.aspects as {_PREFIX}aspects"
    )
    assert "ddtrace_aspects.str_aspect(" in new_code


@pytest.mark.parametrize(
    "module_name",
    [
        ("tests.appsec.iast.fixtures.ast.add_operator.basic"),
    ],
)
def test_astpatch_module_changed_add_operator(module_name):
    module_path, new_ast = astpatch_module(__import__(module_name, fromlist=["*"]))
    assert ("", None) != (module_path, new_ast)
    new_code = unparse(new_ast)
    assert new_code.startswith(
        f"import ddtrace.appsec._iast.sources as {_PREFIX}sources"
        f"\nimport ddtrace.appsec._iast.taint_sinks as {_PREFIX}taint_sinks"
        f"\nimport ddtrace.appsec._iast._taint_tracking.aspects as {_PREFIX}aspects"
    )
    assert "ddtrace_aspects.add_aspect(" in new_code


@pytest.mark.parametrize(
    "module_name",
    [
        ("tests.appsec.iast.fixtures.ast.add_operator.inplace"),
    ],
)
def test_astpatch_module_changed_add_inplace_operator(module_name):
    module_path, new_ast = astpatch_module(__import__(module_name, fromlist=["*"]))
    assert ("", None) != (module_path, new_ast)
    new_code = unparse(new_ast)
    assert new_code.startswith(
        f"import ddtrace.appsec._iast.sources as {_PREFIX}sources"
        f"\nimport ddtrace.appsec._iast.taint_sinks as {_PREFIX}taint_sinks"
        f"\nimport ddtrace.appsec._iast._taint_tracking.aspects as {_PREFIX}aspects"
    )
    assert "ddtrace_aspects.add_inplace_aspect(" in new_code


@pytest.mark.parametrize(
    "module_name",
    [
        ("tests.appsec.iast.fixtures.ast.str.future_import_class_str"),
        ("tests.appsec.iast.fixtures.ast.str.future_import_function_str"),
    ],
)
def test_astpatch_source_changed_with_future_imports(module_name):
    module_path, new_ast = astpatch_module(__import__(module_name, fromlist=["*"]))
    assert ("", None) != (module_path, new_ast)
    new_code = unparse(new_ast)
    assert new_code.startswith(
        f"""\"\"\"\nSome\nmulti-line\ndocstring\nhere\n\"\"\"
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals
import ddtrace.appsec._iast.sources as {_PREFIX}sources
import ddtrace.appsec._iast.taint_sinks as {_PREFIX}taint_sinks
import ddtrace.appsec._iast._taint_tracking.aspects as {_PREFIX}aspects
import html"""
    )
    assert "ddtrace_aspects.str_aspect(" in new_code


@pytest.mark.parametrize(
    "module_name",
    [
        ("tests.appsec.iast.fixtures.ast.str.class_no_str"),
        ("tests.appsec.iast.fixtures.ast.str.function_no_str"),
        ("tests.appsec.iast.fixtures.ast.str.__init__"),  # Empty __init__.py
        ("tests.appsec.iast.fixtures.ast.str.empty_file"),
        ("tests.appsec.iast.fixtures.ast.subscript.store_context"),
    ],
)
def test_astpatch_source_unchanged(module_name):
    assert ("", None) == astpatch_module(__import__(module_name, fromlist=["*"]))


def test_should_iast_patch_allow_first_party():
    assert iastpatch.should_iast_patch("file_in_my_project.main") == iastpatch.ALLOWED_FIRST_PARTY_ALLOWLIST
    assert iastpatch.should_iast_patch("file_in_my_project.print_str") == iastpatch.ALLOWED_FIRST_PARTY_ALLOWLIST
    assert iastpatch.should_iast_patch("html") == iastpatch.ALLOWED_FIRST_PARTY_ALLOWLIST


def test_should_iast_patch_allow_user_allowlist():
    assert iastpatch.should_iast_patch("tests.appsec.iast.integration.main") == iastpatch.ALLOWED_USER_ALLOWLIST
    assert iastpatch.should_iast_patch("tests.appsec.iast.integration.print_str") == iastpatch.ALLOWED_USER_ALLOWLIST


def test_should_not_iast_patch_if_vendored():
    assert iastpatch.should_iast_patch("foobar.vendor.requests") == iastpatch.DENIED_NOT_FOUND
    assert iastpatch.should_iast_patch("vendored.foobar.requests") == iastpatch.DENIED_NOT_FOUND


def test_should_iast_patch_deny_by_default_if_third_party():
    # note that modules here must be in the ones returned by get_package_distributions()
    # but not in ALLOWLIST or DENYLIST. So please don't put astunparse there :)
    assert (
        iastpatch.should_iast_patch("astunparse.foo.bar.not.in.deny.or.allow.list")
        == iastpatch.DENIED_BUILTINS_DENYLIST
    )


def test_should_iast_patch_allow_by_default_if_third_party():
    # note that modules here must be in the ones returned by get_package_distributions()
    # but not in ALLOWLIST or DENYLIST. So please don't put astunparse there :)
    assert iastpatch.should_iast_patch("pygments") == iastpatch.ALLOWED_STATIC_ALLOWLIST
    assert iastpatch.should_iast_patch("pygments.submodule") == iastpatch.ALLOWED_STATIC_ALLOWLIST
    assert iastpatch.should_iast_patch("pygments.submodule.submodule2") == iastpatch.ALLOWED_STATIC_ALLOWLIST


def test_should_not_iast_patch_if_not_in_static_allowlist():
    assert iastpatch.should_iast_patch("ddtrace.internal.module") == iastpatch.DENIED_NOT_FOUND
    assert iastpatch.should_iast_patch("ddtrace.appsec._iast") == iastpatch.DENIED_NOT_FOUND
    assert iastpatch.should_iast_patch("pip.foo.bar") == iastpatch.DENIED_NOT_FOUND


@pytest.mark.parametrize(
    "module_name",
    sorted(
        {
            "__future__",
            "_ast",
            "_compression",
            "_thread",
            "abc",
            "aifc",
            "argparse",
            "array",
            "ast",
            "asynchat",
            "asyncio",
            "asyncore",
            "atexit",
            "audioop",
            "base64",
            "bdb",
            "binascii",
            "bisect",
            "builtins",
            "bz2",
            "cProfile",
            "calendar",
            "cgi",
            "cgitb",
            "chunk",
            "cmath",
            "cmd",
            "code",
            "codecs",
            "codeop",
            "collections",
            "colorsys",
            "compileall",
            "concurrent",
            "configparser",
            "contextlib",
            "contextvars",
            "copy",
            "copyreg",
            "crypt",
            "csv",
            "ctypes",
            "curses",
            "dataclasses",
            "datetime",
            "dbm",
            "decimal",
            "difflib",
            "dis",
            "distutils",
            "doctest",
            "email",
            "encodings",
            "ensurepip",
            "enum",
            "errno",
            "faulthandler",
            "fcntl",
            "filecmp",
            "fileinput",
            "fnmatch",
            "fractions",
            "ftplib",
            "functools",
            "gc",
            "getopt",
            "getpass",
            "gettext",
            "glob",
            "graphlib",
            "grp",
            "gzip",
            "hashlib",
            "heapq",
            "hmac",
            "http",
            "idlelib",
            "imaplib",
            "imghdr",
            "imp",
            "importlib",
            "inspect",
            "io",
            "_io",
            "ipaddress",
            "itertools",
            "json",
            "keyword",
            "lib2to3",
            "linecache",
            "locale",
            "logging",
            "lzma",
            "mailbox",
            "mailcap",
            "marshal",
            "math",
            "mimetypes",
            "mmap",
            "modulefinder",
            "msilib",
            "msvcrt",
            "multiprocessing",
            "netrc",
            "nis",
            "nntplib",
            "ntpath",
            "numbers",
            "opcode",
            "operator",
            "optparse",
            "os",
            "os.path",
            "ossaudiodev",
            "pathlib",
            "pdb",
            "pickle",
            "pickletools",
            "pipes",
            "pkgutil",
            "platform",
            "plistlib",
            "poplib",
            "posix",
            "posixpath",
            "pprint",
            "profile",
            "pstats",
            "pty",
            "pwd",
            "py_compile",
            "pyclbr",
            "pydoc",
            "queue",
            "quopri",
            "random",
            "re",
            "readline",
            "reprlib",
            "resource",
            "rlcompleter",
            "runpy",
            "sched",
            "secrets",
            "select",
            "selectors",
            "shelve",
            "shutil",
            "signal",
            "site",
            "smtpd",
            "smtplib",
            "sndhdr",
            "socket",
            "socketserver",
            "spwd",
            "sqlite3",
            "sre",
            "sre_compile",
            "sre_constants",
            "sre_parse",
            "ssl",
            "stat",
            "statistics",
            "string",
            "stringprep",
            "struct",
            "subprocess",
            "sunau",
            "symtable",
            "sys",
            "sysconfig",
            "syslog",
            "tabnanny",
            "tarfile",
            "telnetlib",
            "tempfile",
            "termios",
            "test",
            "textwrap",
            "threading",
            "time",
            "timeit",
            "tkinter",
            "token",
            "tokenize",
            "tomllib",
            "trace",
            "traceback",
            "tracemalloc",
            "tty",
            "turtle",
            "turtledemo",
            "types",
            "typing",
            "unicodedata",
            "unittest",
            "uu",
            "uuid",
            "venv",
            "warnings",
            "wave",
            "weakref",
            "webbrowser",
            "winreg",
            "winsound",
            "wsgiref",
            "xdrlib",
            "xml",
            "xmlrpc",
            "zipapp",
            "zipfile",
            "zipimport",
            "zlib",
            "zoneinfo",
        }
    ),
)
def test_should_not_iast_patch_if_stdlib(module_name):
    assert iastpatch.should_iast_patch(module_name) == iastpatch.DENIED_BUILTINS_DENYLIST


def test_module_path_none(caplog):
    with caplog.at_level(logging.DEBUG), mock.patch("ddtrace.appsec._iast._ast.ast_patching.origin", return_value=None):
        assert ("", None) == astpatch_module(__import__("tests.appsec.iast.fixtures.ast.str.class_str", fromlist=["*"]))
        assert (
            "iast::instrumentation::ast_patching::compiling::"
            "could not find the module: tests.appsec.iast.fixtures.ast.str.class_str" in caplog.text
        )


@pytest.mark.parametrize(
    "module_name",
    [
        ("tests.appsec.iast.fixtures.ast.io.module_stringio"),
        ("tests.appsec.iast.fixtures.ast.io.function_stringio"),
    ],
)
def test_astpatch_stringio_module_changed(module_name):
    module_path, new_ast = astpatch_module(__import__(module_name, fromlist=["*"]))
    assert ("", None) != (module_path, new_ast)
    new_code = unparse(new_ast)
    assert new_code.startswith(
        f"import ddtrace.appsec._iast.sources as {_PREFIX}sources"
        f"\nimport ddtrace.appsec._iast.taint_sinks as {_PREFIX}taint_sinks"
        f"\nimport ddtrace.appsec._iast._taint_tracking.aspects as {_PREFIX}aspects"
    )
    assert "ddtrace_aspects.stringio_aspect(" in new_code


@pytest.mark.parametrize(
    "module_name",
    [
        ("tests.appsec.iast.fixtures.ast.io.module_bytesio"),
        ("tests.appsec.iast.fixtures.ast.io.function_bytesio"),
    ],
)
def test_astpatch_bytesio_module_changed(module_name):
    module_path, new_ast = astpatch_module(__import__(module_name, fromlist=["*"]))
    assert ("", None) != (module_path, new_ast)
    new_code = unparse(new_ast)
    assert new_code.startswith(
        f"import ddtrace.appsec._iast.sources as {_PREFIX}sources"
        f"\nimport ddtrace.appsec._iast.taint_sinks as {_PREFIX}taint_sinks"
        f"\nimport ddtrace.appsec._iast._taint_tracking.aspects as {_PREFIX}aspects"
    )
    assert "ddtrace_aspects.bytesio_aspect(" in new_code


@pytest.mark.parametrize(
    "module_name",
    [
        ("tests.appsec.iast.fixtures.ast.other.globals_builtin"),
    ],
)
def test_astpatch_globals_module_unchanged(module_name):
    """
    This is a regression test for partially matching function names:
    ``globals()`` was being incorrectly patched with the aspect for ``glob()``
    """
    module_path, new_ast = astpatch_module(__import__(module_name, fromlist=["*"]))
    assert ("", None) == (module_path, new_ast)


@pytest.mark.parametrize(
    "module_name, env_var",
    [
        ("tests.appsec.iast.fixtures.ast.other.with_implemented_dir", "false"),
        ("tests.appsec.iast.fixtures.ast.other.with_implemented_dir", "true"),
        ("tests.appsec.iast.fixtures.ast.other.without_implemented_dir", "false"),
        ("tests.appsec.iast.fixtures.ast.other.without_implemented_dir", "true"),
    ],
)
def test_astpatch_dir_patched_with_env_var(module_name, env_var):
    """
    Check that the ast_patching._DIR_WRAPPER code is added to the end of the module if
    the env var is False and not added otherwise
    """
    with override_env({IAST.ENV_NO_DIR_PATCH: env_var}):
        module_path, new_ast = astpatch_module(__import__(module_name, fromlist=["*"]))
        assert ("", None) != (module_path, new_ast)
        new_code = unparse(new_ast)

        if asbool(env_var):
            # If ENV_NO_DIR_PATCH is set to True our added symbols are not filtered out
            assert f"{_PREFIX}aspects" in new_code
            assert f"{_PREFIX}taint_sinks" in new_code
        else:
            # Check that the added dir code is there
            assert f"def {_PREFIX}dir" in new_code
            assert f"def {_PREFIX}set_dir_filter()" in new_code


@pytest.mark.parametrize(
    "module_name, expected_names",
    [
        (
            "tests.appsec.iast.fixtures.ast.other.with_implemented_dir",
            {"custom_added", "symbol1", "symbol2", "symbol3", "symbol4"},
        ),
        (
            "tests.appsec.iast.fixtures.ast.other.without_implemented_dir",
            {"symbol1", "symbol2", "symbol3", "symbol4"},
        ),
    ],
)
def test_astpatch_dir_patched_with_or_without_custom_dir(module_name, expected_names):
    """
    Check that the patched dir doesn't have any __ddtrace symbols and match the original
    unpatched dir() output, both with or without a previous custom __dir__ implementation
    """
    with override_env({IAST.ENV_NO_DIR_PATCH: "false"}):
        imported_mod = __import__(module_name, fromlist=["*"])
        orig_dir = set(dir(imported_mod))

        for name in expected_names:
            assert name in orig_dir

        module_path, new_ast = astpatch_module(imported_mod)
        assert ("", None) != (module_path, new_ast)

        new_code = unparse(new_ast)
        assert f"def {_PREFIX}dir" in new_code
        assert f"def {_PREFIX}set_dir_filter()" in new_code

        compiled_code = compile(new_ast, module_path, "exec")
        exec(compiled_code, imported_mod.__dict__)
        patched_dir = set(dir(imported_mod))
        for symbol in patched_dir:
            assert not symbol.startswith(_PREFIX)

        # Check that there are no new symbols that were not in the original dir() call
        assert len(patched_dir.difference(orig_dir)) == 0

        # Check that all the symbols in the expected set are in the patched dir() result
        for name in expected_names:
            assert name in patched_dir


def test_package_list_read_inside_a_finder_query_is_not_kept(tmp_path):
    """A module imported by a custom finder while it lists its distributions makes
    IAST read a package list without them: IAST must read it again on the next module.
    """
    from importlib.machinery import PathFinder
    import importlib.metadata as importlib_metadata
    import sys

    from ddtrace.appsec._iast._ast import ast_patching
    from ddtrace.internal import packages

    info = tmp_path / "zz_from_finder-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Name: zz-from-finder\nVersion: 1.0\n")
    (info / "top_level.txt").write_text("zz_from_finder\n")

    class Finder:
        def find_spec(self, *args, **kwargs):
            return None

        def find_distributions(self, context):
            # What the import hook does for a module the finder imports.
            ast_patching._should_iast_patch("imported_by_the_finder")
            return [importlib_metadata.PathDistribution(info)]

    def reset():
        packages._reset_installed_distributions()
        packages._clear_lookup_caches()

    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sys, "meta_path", [Finder(), PathFinder])
            mp.setattr(ast_patching, "IAST_PATCHING_LAZY_LOADED", True)
            reset()

            # Something other than IAST lists the distributions first.
            packages.get_package_distributions()
            assert ast_patching.IAST_PATCHING_LAZY_LOADED

            ast_patching._should_iast_patch("app")
            assert not ast_patching.IAST_PATCHING_LAZY_LOADED
            assert iastpatch.should_iast_patch("zz_from_finder.mod") == iastpatch.DENIED_NOT_FOUND
    finally:
        reset()
        ast_patching.initialize_iast_lists()


@pytest.mark.subprocess(
    env=dict(DD_IAST_ENABLED="true", _DD_IAST_PATCH_MODULES="app_allowed", _DD_IAST_DENY_MODULES="app_denied"),
    parametrize=dict(_DD_TEST_DISCOVERY=("empty", "filesystem", "iast", "cold")),
)
def test_finder_import_uses_only_name_rules_until_discovery_completes():
    from importlib import import_module
    from importlib.machinery import PathFinder
    from importlib.machinery import SourceFileLoader
    from importlib.metadata import PathDistribution
    from importlib.util import spec_from_file_location
    import os
    from pathlib import Path
    import sys
    from tempfile import TemporaryDirectory
    from unittest.mock import patch

    from ddtrace.appsec._iast import enable_iast_propagation
    from ddtrace.appsec._iast._ast import ast_patching
    from ddtrace.internal import packages

    entry = os.environ["_DD_TEST_DISCOVERY"]
    enable_iast_propagation()
    with TemporaryDirectory() as directory:
        root = Path(directory)
        app = root / "app"
        app.mkdir()
        lib = app / "site-packages"
        lib.mkdir()
        source = "def combine(a, b):\n    return a + b\n"
        for name in ("app_helper", "app_allowed", "app_denied", "finder_dep", "installed_dep"):
            (app / f"{name}.py").write_text(source)
        (lib / "lib_dep.py").write_text(source)

        def dist(where, name):
            info = where / f"{name}-1.0.dist-info"
            info.mkdir()
            (info / "METADATA").write_text(f"Name: {name}\nVersion: 1.0\n")
            (info / "top_level.txt").write_text(name)
            return PathDistribution(info)

        dependency = dist(root, "finder_dep")  # Only the finder knows about this distribution.
        if entry == "filesystem":
            dist(app, "installed_dep")

        class Loader(SourceFileLoader):
            def exec_module(self, module):
                module.loader_ran = True
                super().exec_module(module)

        class Finder:
            calls = 0

            def find_spec(self, fullname, *args):
                if fullname == "finder_dep":
                    path = str(app / "finder_dep.py")
                    return spec_from_file_location(fullname, path, loader=Loader(fullname, path))
                return None

            def find_distributions(self, context):
                self.calls += 1
                if self.calls == 1:
                    for name in ("app_helper", "app_allowed", "app_denied", "finder_dep", "installed_dep", "lib_dep"):
                        import_module(name)
                return [dependency]

        finder = Finder()
        meta_path = sys.meta_path[:]
        meta_path.insert(meta_path.index(PathFinder), finder)
        path = [str(app), str(lib), str(packages.stdlib_path), str(packages.stdlib_path / "lib-dynload")]
        with patch.object(sys, "meta_path", meta_path), patch.object(sys, "path", path):
            if entry == "cold":
                sys.modules.pop("importlib.metadata", None)
                sys.modules["importlib"].__dict__.pop("metadata", None)
            packages._reset_installed_distributions()
            ast_patching.IAST_PATCHING_LAZY_LOADED = True
            # A pre-existing native list must not authorize first-party patching either.
            ast_patching.iastpatch.set_packages_distributions({"other_package"})
            if entry == "iast":
                ast_patching._should_iast_patch("app_entry")
            else:
                packages.get_package_distributions()
            assert finder.calls == 1

            def instrumented(name):
                return "add_aspect" in sys.modules[name].combine.__code__.co_names

            assert not instrumented("app_helper")
            assert instrumented("app_allowed")
            assert not instrumented("app_denied")
            assert not instrumented("lib_dep")
            assert sys.modules["finder_dep"].loader_ran
            assert not instrumented("finder_dep")
            assert not instrumented("installed_dep")
            # Now use the completed inventory, even for code outside the finder.
            assert not ast_patching._should_iast_patch("finder_dep.next")
            assert not ast_patching.IAST_PATCHING_LAZY_LOADED


def test_ddtrace_run_finder_imports_during_startup(tmp_path, python_process, monkeypatch):
    for name in ("finder_helper", "finder_allowed", "finder_dependency", "later_helper"):
        (tmp_path / f"{name}.py").write_text("def combine(a, b):\n    return a + b\n")
    fixture = Path(__file__).parents[1] / "fixtures" / "distribution_finder"
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(fixture), str(tmp_path))), prepend=os.pathsep)
    monkeypatch.setenv("DD_IAST_ENABLED", "true")
    monkeypatch.setenv("_DD_IAST_PATCH_MODULES", "finder_allowed")
    python_process(
        dedent(
            """\
            import sys

            # These modules must already have executed inside the startup query.
            def patched(module):
                return "add_aspect" in module.combine.__code__.co_names

            assert not patched(sys.modules["finder_helper"])
            assert patched(sys.modules["finder_allowed"])
            dependency = sys.modules["finder_dependency"]
            assert dependency.loader_ran and not patched(dependency)

            import later_helper
            assert patched(later_helper)
            """
        ),
        ddtrace_run=True,
    ).check_returncode()
