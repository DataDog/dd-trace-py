#!/usr/bin/env python3
from ast import unparse
import logging
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


@pytest.mark.subprocess(
    env=dict(DD_IAST_ENABLED="true", _DD_IAST_PATCH_MODULES="", _DD_IAST_DENY_MODULES=""),
    parametrize=dict(_DD_TEST_ENTRY=("packages", "iast", "native", "cached", "cold", "retry")),
)
def test_finder_import_preserves_legacy_iast_classification():
    from importlib import import_module
    from importlib.machinery import PathFinder
    from importlib.machinery import SourceFileLoader
    from importlib.util import spec_from_file_location
    import os
    from pathlib import Path
    import sys
    from tempfile import TemporaryDirectory

    if sys.version_info < (3, 10):
        import importlib_metadata as metadata
    else:
        import importlib.metadata as metadata

    from ddtrace.appsec._iast import enable_iast_propagation
    from ddtrace.appsec._iast._ast import ast_patching as ap
    from ddtrace.internal import packages
    from ddtrace.internal.threads import Thread

    metadata.version("ddtrace")

    def legacy_inventory_is_unused():
        raise AssertionError("IAST must use the native package resolver")

    metadata.packages_distributions = legacy_inventory_is_unused
    entry = os.environ["_DD_TEST_ENTRY"]
    observations = []
    with TemporaryDirectory() as directory:
        root = Path(directory)
        app = root / "app"
        app.mkdir()
        source = "def combine(a, b):\n    return a + b\n"
        (app / "customer_helper.py").write_text(source)
        (app / "customer_next.py").write_text(source)
        (app / "finder_dependency.py").write_text("assert loader_ready\n" + source)
        info = (app if entry == "native" else root) / "finder_bundle-1.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text("Name: finder-bundle\nVersion: 1.0\n")
        (info / "top_level.txt").write_text("ddtrace\nfinder_dependency\n")

        class Loader(SourceFileLoader):
            def exec_module(self, module):
                module.loader_ready = True
                return super().exec_module(module)

        class Finder:
            ready = True
            calls = 0

            def find_spec(self, fullname, *args, **kwargs):
                if fullname == "finder_dependency":
                    path = app / "finder_dependency.py"
                    return spec_from_file_location(fullname, path, loader=Loader(fullname, str(path)))
                return None

            def find_distributions(self, context):
                self.calls += 1
                if entry == "retry" and self.calls == 2:
                    raise RuntimeError("finder cannot complete the reentrant query")
                if not self.ready:
                    return [metadata.PathDistribution(info)]
                helper = import_module("customer_helper")
                dependency = import_module("finder_dependency")
                # A recursive import can see a partially initialized module.
                if hasattr(helper, "combine") and hasattr(dependency, "combine"):
                    observations.append(
                        (
                            "add_aspect" in helper.combine.__code__.co_names,
                            "add_aspect" in dependency.combine.__code__.co_names,
                            dependency.loader_ready,
                        )
                    )
                return [metadata.PathDistribution(info)]

        finder = Finder()
        original_path = sys.path[:]
        original_meta_path = sys.meta_path[:]
        try:
            sys.path[:] = [str(app), str(packages.stdlib_path), str(packages.stdlib_path / "lib-dynload")]
            if entry == "native":
                sys.meta_path[:] = [PathFinder]
            else:
                sys.meta_path.insert(sys.meta_path.index(PathFinder), finder)
            enable_iast_propagation()
            if entry == "cached":
                finder.ready = False
                packages.get_package_distributions()
            else:
                packages._reset_installed_distributions()
            packages._clear_lookup_caches()
            ap.iastpatch.set_packages_distributions(set())
            ap.IAST_PATCHING_LAZY_LOADED = True
            if entry in ("native", "cached"):
                for name in ("importlib.metadata", "importlib_metadata", "zipfile"):
                    sys.modules.pop(name, None)
                worker = Thread(ap.initialize_iast_lists, name="iast-inventory")
                worker.start()
                worker.join()
                assert not {"importlib.metadata", "importlib_metadata", "zipfile"} & sys.modules.keys()
                assert (
                    ap.iastpatch.should_iast_patch("zz_iast_native_probe") == ap.iastpatch.ALLOWED_FIRST_PARTY_ALLOWLIST
                )
            elif entry in ("packages", "cold", "retry"):
                if entry == "cold":
                    sys.modules.pop("importlib.metadata", None)
                    sys.modules["importlib"].__dict__.pop("metadata", None)
                packages.get_package_distributions()
            else:
                ap._should_iast_patch("customer_entry")
            if entry not in ("native", "cached"):
                assert observations
                if entry in ("cold", "retry"):
                    assert all(row[1:] == (False, True) for row in observations), observations
                    following = import_module("customer_next")
                    assert "add_aspect" in following.combine.__code__.co_names
                else:
                    assert all(row == (True, False, True) for row in observations), observations
                assert ap.iastpatch.should_iast_patch("finder_dependency") == ap.iastpatch.DENIED_NOT_FOUND
        finally:
            sys.path[:] = original_path
            sys.meta_path[:] = original_meta_path
