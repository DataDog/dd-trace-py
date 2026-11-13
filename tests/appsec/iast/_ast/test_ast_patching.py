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


@pytest.mark.parametrize("invalid, error", [([None], TypeError), (["new_dependency", "\ud800"], UnicodeEncodeError)])
def test_package_publication_failure_preserves_previous_inventory(invalid, error):
    from ddtrace.appsec._iast._ast import ast_patching

    try:
        iastpatch.set_packages_distributions({"previous_dependency"})
        with pytest.raises(error):
            iastpatch.set_packages_distributions(invalid)
        assert iastpatch.should_iast_patch("previous_dependency") == iastpatch.DENIED_NOT_FOUND
        assert iastpatch.should_iast_patch("new_dependency") == iastpatch.ALLOWED_FIRST_PARTY_ALLOWLIST
    finally:
        ast_patching.initialize_iast_lists()


@pytest.mark.parametrize(
    "kind",
    [
        "ordinary",
        "subclass",
        "instance",
        "class",
        "bound_exec",
        "bound_code",
        "custom_code",
        "mismatched",
        "site",
        "symlink",
        "no_spec",
        "module_subclass",
    ],
)
def test_discovery_source_eligibility(tmp_path, monkeypatch, kind):
    from importlib.machinery import SourceFileLoader
    from importlib.util import spec_from_file_location
    import sys
    from types import ModuleType

    from ddtrace.appsec._iast._ast import ast_patching as ap

    name = "eligibility_customer"
    source = tmp_path / "customer.py"
    source.write_text("value = 1\n")
    if kind in ("site", "symlink"):
        installed = tmp_path / "site-packages" / "customer.py"
        installed.parent.mkdir()
        if kind == "symlink":
            installed.symlink_to(source)
        else:
            installed.write_text("value = 1\n")
        source = installed
    loader = SourceFileLoader(name, str(source))
    if kind == "subclass":

        class CustomLoader(SourceFileLoader):
            pass

        loader = CustomLoader(name, str(source))
    elif kind == "instance":
        loader.exec_module = lambda module: None
    elif kind == "class":
        monkeypatch.setattr(SourceFileLoader, "exec_module", lambda self, module: None)
    elif kind in ("bound_exec", "bound_code"):
        other = SourceFileLoader(name, str(tmp_path / "other.py"))
        method = "exec_module" if kind == "bound_exec" else "get_code"
        setattr(loader, method, getattr(other, method))
    elif kind == "custom_code":
        loader.get_code = lambda name: None
    module = ModuleType(name)
    if kind == "module_subclass":

        class CustomModule(ModuleType):
            pass

        module = CustomModule(name)
    module.__spec__ = spec_from_file_location(name, source, loader=loader)
    if kind == "mismatched":
        module.__spec__.origin = str(tmp_path / "different.py")
    module.__file__ = module.__spec__.origin
    if kind == "no_spec":
        module.__spec__ = None
    monkeypatch.setitem(sys.modules, name, module)
    assert ap._is_application_source(name) is (kind == "ordinary")


@pytest.mark.subprocess(
    env=dict(DD_IAST_ENABLED="true", _DD_IAST_PATCH_MODULES=""),
    parametrize=dict(_DD_TEST_LOADER_SETUP=("exec_module", "set_data", "_cache_bytecode")),
)
def test_discovery_import_preserves_custom_loader_setup():
    import builtins
    from importlib import import_module
    from importlib.machinery import PathFinder
    from importlib.machinery import SourceFileLoader
    from importlib.util import spec_from_file_location
    import os
    from pathlib import Path
    import sys
    from tempfile import TemporaryDirectory

    from ddtrace.appsec._iast import enable_iast_propagation
    from ddtrace.appsec._iast._ast import ast_patching as ap
    from ddtrace.internal import packages

    builtins._iast_loader_ready = False
    sys.dont_write_bytecode = False
    mode = os.environ["_DD_TEST_LOADER_SETUP"]
    with TemporaryDirectory() as directory:
        path = Path(directory) / "loader_customer.py"
        path.write_text("import builtins\nassert builtins._iast_loader_ready\ndef combine(a, b):\n    return a + b\n")

        class Loader(SourceFileLoader):
            def exec_module(self, module):
                builtins._iast_loader_ready = True
                return super().exec_module(module)

        class Finder:
            def find_spec(self, fullname, *args, **kwargs):
                if fullname == "loader_customer":
                    loader = (
                        Loader(fullname, str(path)) if mode == "exec_module" else SourceFileLoader(fullname, str(path))
                    )
                    if mode != "exec_module":
                        original = getattr(loader, mode)

                        def setup(*args, **kwargs):
                            builtins._iast_loader_ready = True
                            return original(*args, **kwargs)

                        setattr(loader, mode, setup)
                    return spec_from_file_location(fullname, path, loader=loader)
                return None

        sys.meta_path.insert(sys.meta_path.index(PathFinder), Finder())
        enable_iast_propagation()
        ap.iastpatch.set_packages_distributions({"ddtrace", "known"})
        ap.IAST_PATCHING_LAZY_LOADED = False
        packages._FINDER_QUERY.active = True
        try:
            module = import_module("loader_customer")
        finally:
            packages._FINDER_QUERY.active = False
        assert builtins._iast_loader_ready
        assert "add_aspect" not in module.combine.__code__.co_names


@pytest.mark.subprocess(parametrize=dict(_DD_TEST_FAILURE=("discovery", "setter")))
def test_failed_initialization_after_finder_reentry_is_retried():
    import os
    from unittest import mock

    from ddtrace.appsec._iast._ast import ast_patching as ap
    from ddtrace.internal import packages

    known = {"known_dependency": ["known"]}
    ap.iastpatch.set_packages_distributions(set(known))
    setter = ap.iastpatch.set_packages_distributions
    setter_calls = []

    def publish(names):
        setter_calls.append(names)
        if os.environ["_DD_TEST_FAILURE"] == "setter" and len(setter_calls) == 1:
            raise RuntimeError("publication failed")
        return setter(names)

    failure = RuntimeError("discovery failed") if os.environ["_DD_TEST_FAILURE"] == "discovery" else (known, True)
    ap.IAST_PATCHING_LAZY_LOADED = True
    with (
        mock.patch.object(
            ap, "get_package_distributions_with_status", side_effect=[({}, False), failure, (known, True)]
        ) as discover,
        mock.patch.object(ap.iastpatch, "set_packages_distributions", side_effect=publish),
    ):
        packages._FINDER_QUERY.active = True
        try:
            ap._should_iast_patch("application_during_discovery")
        finally:
            packages._FINDER_QUERY.active = False
        assert not setter_calls
        assert not ap._should_iast_patch("known_dependency")
        assert ap.IAST_PATCHING_LAZY_LOADED
        assert ap._should_iast_patch("application_next")
        assert not ap.IAST_PATCHING_LAZY_LOADED
        assert not ap._should_iast_patch("known_dependency")
        assert discover.call_count == 3


@pytest.mark.subprocess(parametrize=dict(_DD_TEST_DISCOVERY_CONTEXT=("finder", "prefetch")))
def test_discovery_reentry_after_completed_initialization_stays_local():
    import os
    from unittest import mock

    from ddtrace.appsec._iast._ast import ast_patching as ap
    from ddtrace.internal import packages

    # A later finder query or prefetch can expose an unpublished view after IAST
    # was initialized already; this must still arrange a refresh for the next import.
    ap.iastpatch.set_packages_distributions({"previous_dependency"})
    ap.IAST_PATCHING_LAZY_LOADED = False
    context = packages._FINDER_QUERY if os.environ["_DD_TEST_DISCOVERY_CONTEXT"] == "finder" else packages._IN_PREFETCH
    with mock.patch.object(
        ap, "get_package_distributions_with_status", side_effect=[({}, False), ({"known": ["known"]}, True)]
    ):
        context.active = True
        try:
            ap._should_iast_patch("application_during_discovery")
        finally:
            context.active = False
        assert ap.IAST_PATCHING_LAZY_LOADED
        assert ap.iastpatch.should_iast_patch("previous_dependency") == ap.iastpatch.DENIED_NOT_FOUND
        assert ap._should_iast_patch("application")
        assert not ap.IAST_PATCHING_LAZY_LOADED
        assert not ap._should_iast_patch("known")


@pytest.mark.subprocess()
def test_prefetch_package_view_is_not_published_to_iast():
    from importlib.machinery import PathFinder
    import sys
    from unittest import mock

    from ddtrace.appsec._iast._ast import ast_patching as ap
    from ddtrace.internal import packages

    finder_calls = []

    class Finder:
        def find_spec(self, *args, **kwargs):
            return None

        def find_distributions(self, context):
            finder_calls.append(context)
            return []

    packages._reset_installed_distributions()
    packages._clear_lookup_caches()
    ap.iastpatch.set_packages_distributions({"previous_dependency"})
    ap.IAST_PATCHING_LAZY_LOADED = True
    with mock.patch.object(sys, "meta_path", [Finder(), PathFinder]):
        packages._IN_PREFETCH.active = True
        try:
            names, published = packages.get_package_distributions_with_status()
            assert not published
            assert not finder_calls
            assert not packages.is_querying_finders()
            assert packages.is_distribution_discovery_in_progress()
            ap._should_iast_patch("application_during_prefetch")
            assert ap.IAST_PATCHING_LAZY_LOADED
            assert ap.iastpatch.should_iast_patch("previous_dependency") == ap.iastpatch.DENIED_NOT_FOUND
        finally:
            packages._IN_PREFETCH.active = False


@pytest.mark.parametrize(
    "module_name, expected",
    [
        ("customer", iastpatch.ALLOWED_FIRST_PARTY_ALLOWLIST),
        ("user_allowed", iastpatch.ALLOWED_USER_ALLOWLIST),
        ("user_denied", iastpatch.DENIED_USER_DENYLIST),
        ("os", iastpatch.DENIED_BUILTINS_DENYLIST),
        ("pygments", iastpatch.ALLOWED_STATIC_ALLOWLIST),
        ("customer.vendor.requests", iastpatch.DENIED_NOT_FOUND),
        ("vendored.requests", iastpatch.DENIED_NOT_FOUND),
        ("vendored", iastpatch.DENIED_NOT_FOUND),
        ("app.vendor", iastpatch.DENIED_NOT_FOUND),
        ("app._vendor", iastpatch.DENIED_NOT_FOUND),
        ("ddtrace.internal.module", iastpatch.DENIED_NOT_FOUND),
        ("mysqlsh", iastpatch.DENIED_STATIC_DENYLIST),
        ("partial_dependency", iastpatch.DENIED_NOT_FOUND),
        ("previous_dependency", iastpatch.DENIED_NOT_FOUND),
    ],
)
def test_incomplete_package_list_preserves_patch_rules(module_name, expected):
    from ddtrace.appsec._iast._ast import ast_patching

    try:
        with override_env({IAST.PATCH_MODULES: "user_allowed", IAST.DENY_MODULES: "user_denied"}):
            iastpatch.build_list_from_env(IAST.PATCH_MODULES)
            iastpatch.build_list_from_env(IAST.DENY_MODULES)
            iastpatch.set_packages_distributions({"previous_dependency"})
            assert iastpatch.should_iast_patch(module_name, {"partial_dependency"}) == expected
    finally:
        ast_patching.initialize_iast_lists()


def test_incomplete_package_list_is_not_cached():
    from ddtrace.appsec._iast._ast import ast_patching

    try:
        iastpatch.set_packages_distributions(set())
        assert iastpatch.should_iast_patch("customer") == iastpatch.DENIED_NOT_FOUND
        assert iastpatch.should_iast_patch("customer", set()) == iastpatch.DENIED_NOT_FOUND
        assert iastpatch.should_iast_patch("customer") == iastpatch.DENIED_NOT_FOUND
        with pytest.raises(TypeError):
            iastpatch.should_iast_patch("customer", {None})
        assert iastpatch.should_iast_patch("customer") == iastpatch.DENIED_NOT_FOUND
    finally:
        ast_patching.initialize_iast_lists()


@pytest.mark.subprocess()
def test_incomplete_reader_cannot_overwrite_completed_initialization():
    import threading
    from unittest import mock

    from ddtrace.appsec._iast._ast import ast_patching as ap
    from ddtrace.internal import packages

    pending = threading.Event()
    release = threading.Event()
    errors = []

    def inventory():
        if threading.current_thread().name == "incomplete-reader":
            pending.set()
            assert release.wait(5)
            return {}, False
        return {"known_dependency": ["known"]}, True

    def read_partial():
        packages._FINDER_QUERY.active = True
        try:
            ap._should_iast_patch("application_a")
        except BaseException as exc:
            errors.append(exc)
        finally:
            packages._FINDER_QUERY.active = False

    ap.IAST_PATCHING_LAZY_LOADED = True
    with mock.patch.object(ap, "get_package_distributions_with_status", side_effect=inventory):
        thread = threading.Thread(target=read_partial, name="incomplete-reader")
        thread.start()
        try:
            assert pending.wait(5)
            assert ap._should_iast_patch("application_b")
            assert not ap.IAST_PATCHING_LAZY_LOADED
        finally:
            release.set()
            thread.join(5)
        assert not thread.is_alive()
        assert not errors, errors
        assert ap.iastpatch.should_iast_patch("known_dependency") == ap.iastpatch.DENIED_NOT_FOUND
        assert not ap._should_iast_patch("known_dependency")
        assert ap._should_iast_patch("application_next")


@pytest.mark.subprocess()
def test_failed_discovery_reentry_keeps_loader_and_static_guards():
    from importlib.machinery import SourceFileLoader
    from importlib.util import spec_from_file_location
    import sys
    from types import ModuleType
    from unittest import mock

    from ddtrace.appsec._iast._ast import ast_patching as ap
    from ddtrace.internal import packages

    class CustomLoader(SourceFileLoader):
        pass

    module = ModuleType("finder_owned")
    module.__spec__ = spec_from_file_location("finder_owned", __file__, loader=CustomLoader("finder_owned", __file__))
    sys.modules[module.__name__] = module
    ap.iastpatch.set_packages_distributions({"previous_dependency"})
    ap.IAST_PATCHING_LAZY_LOADED = False
    with mock.patch.object(ap, "get_package_distributions_with_status", side_effect=MemoryError):
        packages._FINDER_QUERY.active = True
        try:
            assert not ap._should_iast_patch("mysqlsh")
            assert not ap._should_iast_patch(module.__name__)
            assert ap.IAST_PATCHING_LAZY_LOADED
        finally:
            packages._FINDER_QUERY.active = False


@pytest.mark.subprocess()
def test_completed_reader_cannot_clear_a_newer_retry():
    import threading
    from unittest import mock

    from ddtrace.appsec._iast._ast import ast_patching as ap
    from ddtrace.internal import packages

    pending = threading.Event()
    release = threading.Event()
    errors = []
    initialize = ap.initialize_iast_lists

    def inventory():
        if threading.current_thread().name == "completed-reader":
            return {"old_dependency": ["old"]}, True
        if packages.is_distribution_discovery_in_progress():
            return {}, False
        return {"new_dependency": ["new"]}, True

    def pause_after_publication():
        result = initialize()
        if threading.current_thread().name == "completed-reader":
            pending.set()
            assert release.wait(5)
        return result

    def read_complete():
        try:
            ap._should_iast_patch("application_a")
        except BaseException as exc:
            errors.append(exc)

    ap.IAST_PATCHING_LAZY_LOADED = True
    with (
        mock.patch.object(ap, "get_package_distributions_with_status", side_effect=inventory),
        mock.patch.object(ap, "initialize_iast_lists", side_effect=pause_after_publication),
    ):
        thread = threading.Thread(target=read_complete, name="completed-reader")
        thread.start()
        try:
            assert pending.wait(5)
            packages._FINDER_QUERY.active = True
            try:
                ap._should_iast_patch("application_during_later_discovery")
            finally:
                packages._FINDER_QUERY.active = False
            assert ap.IAST_PATCHING_LAZY_LOADED
        finally:
            release.set()
            thread.join(5)
        assert not thread.is_alive()
        assert not errors, errors
        assert ap.IAST_PATCHING_LAZY_LOADED
        assert not ap._should_iast_patch("new_dependency")
        assert not ap.IAST_PATCHING_LAZY_LOADED


@pytest.mark.subprocess(
    env=dict(DD_IAST_ENABLED="true", _DD_IAST_PATCH_MODULES="", _DD_IAST_DENY_MODULES=""),
    parametrize=dict(
        _DD_TEST_NATIVE_PACKAGES=("nonempty",), _DD_TEST_DISCOVERY=("packages", "iast", "version", "available")
    ),
)
def test_application_imported_during_discovery_is_instrumented_immediately():
    import builtins
    from importlib import import_module
    from importlib.machinery import PathFinder
    import importlib.metadata as metadata
    import os
    from pathlib import Path
    import sys
    from tempfile import TemporaryDirectory

    from ddtrace.appsec._iast import enable_iast_propagation
    from ddtrace.appsec._iast._ast import ast_patching as ap
    from ddtrace.internal import packages

    metadata.version("ddtrace")
    with TemporaryDirectory() as directory:
        root = Path(directory)
        app = root / "app"
        app.mkdir()
        source = "def combine(a, b):\n    return a + b\n"
        (app / "customer_helper.py").write_text(source)
        denied_source = "import builtins\nbuiltins._iast_imports.append(__name__)\nglobals = []\n" + source
        (app / "mysqlsh.py").write_text(denied_source)
        (app / "vendored").mkdir()
        (app / "vendored" / "__init__.py").write_text(denied_source)
        builtins._iast_imports = []
        info = root / "finder_dependency-1.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text("Name: finder-dependency\nVersion: 1.0\n")
        (info / "top_level.txt").write_text("finder_dependency\n")
        if os.environ["_DD_TEST_NATIVE_PACKAGES"] == "nonempty":
            native = app / "native_package-1.0.dist-info"
            native.mkdir()
            (native / "METADATA").write_text("Name: native-package\nVersion: 1.0\n")
            (native / "top_level.txt").write_text("native_package\n")
        observations = []
        errors = []

        class Finder:
            ready = True

            def find_spec(self, *args, **kwargs):
                return None

            def find_distributions(self, context):
                if not self.ready:
                    return []
                try:
                    modules = [import_module(name) for name in ("customer_helper", "mysqlsh", "vendored")]
                    observations.append(tuple("add_aspect" in m.combine.__code__.co_names for m in modules))
                except Exception as exc:
                    errors.append(repr(exc))
                return [metadata.PathDistribution(info)]

        packages._reset_installed_distributions()
        packages._clear_lookup_caches()
        finder = Finder()
        sys.path[:] = [str(app), str(packages.stdlib_path), str(packages.stdlib_path / "lib-dynload")]
        sys.meta_path.insert(sys.meta_path.index(PathFinder), finder)
        enable_iast_propagation()
        ap.iastpatch.set_packages_distributions(set())
        ap.IAST_PATCHING_LAZY_LOADED = True
        discovery = os.environ["_DD_TEST_DISCOVERY"]
        if discovery in ("version", "available"):
            finder.ready = False
            packages.get_package_distributions()
            finder.ready = True
            ap.IAST_PATCHING_LAZY_LOADED = False
            if discovery == "version":
                assert packages.get_version_for_package("finder-dependency") == "1.0"
            else:
                assert packages.is_distribution_available("finder-dependency")
        elif discovery == "packages":
            packages.get_package_distributions()
        else:
            ap._should_iast_patch("customer_entry")
        assert not errors, errors
        assert observations and all(row == (True, False, False) for row in observations)
        assert builtins._iast_imports == ["mysqlsh", "vendored"]
        assert ap._should_iast_patch("customer_next")
        assert not ap._should_iast_patch("finder_dependency")
