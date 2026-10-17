#!/usr/bin/env python3

import os
import subprocess
import sys
import textwrap

import pytest

from ddtrace.appsec._shared._stacktrace import get_info_frame


def test_stacktrace():
    file, line, function, class_ = get_info_frame()
    import traceback

    traceback.print_stack()
    assert file is not None
    assert file.endswith("test_stacktrace.py")
    assert line is not None
    assert function == "test_stacktrace"
    assert class_ == ""


async def test_stacktrace_async():
    async def _inner():
        return get_info_frame()

    file, line, function, class_ = await _inner()
    assert file is not None
    assert file.endswith("test_stacktrace.py")
    assert line is not None
    assert function == "_inner"
    assert class_ is not None


async def test_stacktrace_async_no_relevant_frame():
    """
    In the absence of any non-ddtrace and non-stdlib frame in the stacktrace, no frame is returned.
    (And no exception is raised).
    """
    import asyncio

    file, line, function, class_ = await asyncio.to_thread(get_info_frame)
    assert file is None
    assert line is None
    assert function is None
    assert class_ is None


class _Holder:
    def caller(self):
        return get_info_frame()

    class _Nested:
        def caller(self):
            return get_info_frame()


def test_stacktrace_class_name():
    """A method frame reports the class that owns it."""
    file, line, function, class_ = _Holder().caller()
    assert file is not None
    assert file.endswith("test_stacktrace.py")
    assert line is not None
    assert function == "caller"
    assert class_ == "_Holder"
    assert _Holder()._Nested().caller()[3] == "_Nested"


@pytest.mark.parametrize("attribute", ["__class__", "__name__"])
@pytest.mark.parametrize("raises", [False, True])
def test_stacktrace_class_name_ignores_attribute_hooks(attribute, raises):
    def get_attribute(obj, name, get_original):
        if name == attribute:
            if raises:
                raise RuntimeError("Class metadata is unavailable")
            return 123
        return get_original(obj, name)

    class Metaclass(type):
        def __getattribute__(cls, name):
            return get_attribute(cls, name, type.__getattribute__)

    class Holder(_Holder, metaclass=Metaclass):
        def __getattribute__(self, name):
            return get_attribute(self, name, object.__getattribute__)

    assert Holder().caller()[3] == "Holder"


def test_stacktrace_reentrant_locals():
    nested = []

    class Locals(dict):
        def __getitem__(self, key):
            if key == "self":
                nested.append(get_info_frame())
            return super().__getitem__(key)

    namespace = Locals()
    exec(compile("result = get_info_frame()", __file__, "exec"), {"get_info_frame": get_info_frame}, namespace)
    assert nested == [(None, None, None, None)]
    assert namespace["result"][0] == __file__
    assert namespace["result"][3] == ""
    assert _Holder().caller()[3] == "_Holder"


@pytest.mark.parametrize("installed_application", [False, True])
def test_stacktrace_path_filtering(tmp_path, installed_application):
    stdlib = tmp_path / "stdlib"
    purelib = stdlib / "site-packages"
    purelib.mkdir(parents=True)
    alias = tmp_path / "stdlib-link"
    alias.symlink_to(stdlib, target_is_directory=True)

    # Roots are cached at extension initialization, so use a fresh interpreter.
    code = textwrap.dedent("""
        from pathlib import Path
        import sys
        import sysconfig

        import ddtrace
        import _ddtrace_internal

        assert "ddtrace.appsec._shared._stacktrace" not in sys.modules
        stdlib = Path(sys.argv[1])
        alias = Path(sys.argv[2])
        installed_application = sys.argv[3] == "True"
        get_path = sysconfig.get_path
        paths = {"stdlib": str(alias), "purelib": str(alias / "site-packages")}
        sysconfig.get_path = lambda name: paths[name] if name in paths else get_path(name)

        from ddtrace.appsec._shared._stacktrace import get_info_frame

        def caller_at(filename, callee):
            namespace = {"callee": callee}
            exec(compile("def caller():\\n    return callee()\\n", str(filename), "exec"), namespace)
            return namespace["caller"]

        application = (alias / "site-packages" if installed_application else stdlib.parent) / "my_app" / "views.py"

        def capture(inner):
            return caller_at(application, caller_at(inner, get_info_frame))()

        for package in (ddtrace, _ddtrace_internal):
            root = Path(package.__file__).parent
            assert capture(root / "wrapper.py")[0] == str(application)
            sibling = root.with_name(root.name + "_tools") / "views.py"
            assert caller_at(sibling, get_info_frame)()[0] == str(sibling)

        for root in (stdlib, alias):
            assert capture(root / "threading.py")[0] == str(application)

        dependency = alias / "site-packages" / "dependency" / "wrapper.py"
        expected = dependency if installed_application else application
        assert capture(dependency)[0] == str(expected)
    """)
    result = subprocess.run(
        [sys.executable, "-c", code, str(stdlib), str(alias), str(installed_application)],
        env=dict(os.environ, DD_IAST_ENABLED="false"),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
