from importlib.util import module_from_spec
from importlib.util import spec_from_file_location
import os
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest


SOURCE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "lib-injection", "sources", "_ddtrace_uwsgi.py")
)


def _load_helper():
    spec = spec_from_file_location("ddtrace_uwsgi_test", SOURCE)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_decorators(monkeypatch):
    chain = []
    decorators = ModuleType("uwsgidecorators")

    def postfork_chain_hook():
        for callback in chain:
            callback()

    def postfork(callback):
        chain.append(callback)

    decorators.postfork = postfork
    decorators.postfork_chain_hook = postfork_chain_hook
    monkeypatch.setitem(sys.modules, "uwsgidecorators", decorators)
    return decorators


def test_defer_injection_outside_uwsgi(monkeypatch):
    monkeypatch.delitem(sys.modules, "uwsgi", raising=False)
    monkeypatch.setattr(sys, "executable", "/usr/bin/python")
    inject = MagicMock()

    assert _load_helper().defer_injection(inject) is False
    inject.assert_not_called()


def test_defer_injection_with_missing_executable_outside_uwsgi(monkeypatch):
    monkeypatch.delitem(sys.modules, "uwsgi", raising=False)
    monkeypatch.setattr(sys, "executable", None)
    monkeypatch.setattr(sys, "argv", ["python"])
    monkeypatch.setattr(os, "readlink", MagicMock(side_effect=OSError))
    inject = MagicMock()

    assert _load_helper().defer_injection(inject) is False
    inject.assert_not_called()


def test_defer_injection_ignores_uwsgi_script_name(monkeypatch):
    monkeypatch.delitem(sys.modules, "uwsgi", raising=False)
    monkeypatch.setattr(sys, "argv", ["/tmp/uwsgi-healthcheck.py"])
    monkeypatch.setattr(os, "readlink", lambda path: "/usr/bin/python")
    settrace = MagicMock()
    monkeypatch.setattr(sys, "settrace", settrace)
    inject = MagicMock()

    assert _load_helper().defer_injection(inject) is False
    settrace.assert_not_called()
    inject.assert_not_called()


def test_defer_injection_when_uwsgi_is_ready(monkeypatch):
    uwsgi = ModuleType("uwsgi")
    uwsgi.masterpid = lambda: 123
    uwsgi.opt = {}
    uwsgi.numproc = 1
    uwsgi.worker_id = lambda: 1
    monkeypatch.setitem(sys.modules, "uwsgi", uwsgi)
    inject = MagicMock()

    assert _load_helper().defer_injection(inject) is False
    inject.assert_not_called()


def test_defer_injection_until_uwsgi_worker(monkeypatch):
    uwsgi = ModuleType("uwsgi")
    uwsgi.masterpid = lambda: 123
    previous_callback = MagicMock()
    uwsgi.post_fork_hook = previous_callback
    monkeypatch.setitem(sys.modules, "uwsgi", uwsgi)
    decorators = _install_decorators(monkeypatch)
    inject = MagicMock()

    assert _load_helper().defer_injection(inject) is True
    inject.assert_not_called()

    application_callback = MagicMock()
    decorators.postfork(application_callback)
    uwsgi.post_fork_hook()

    previous_callback.assert_called_once_with()
    inject.assert_called_once_with()
    application_callback.assert_called_once_with()


def test_defer_injection_runs_when_previous_postfork_callback_raises(monkeypatch):
    uwsgi = ModuleType("uwsgi")
    uwsgi.masterpid = lambda: 123
    previous_callback = MagicMock(side_effect=RuntimeError("postfork failed"))
    uwsgi.post_fork_hook = previous_callback
    monkeypatch.setitem(sys.modules, "uwsgi", uwsgi)
    _install_decorators(monkeypatch)
    inject = MagicMock()

    assert _load_helper().defer_injection(inject) is True

    with pytest.raises(RuntimeError, match="postfork failed"):
        uwsgi.post_fork_hook()
    previous_callback.assert_called_once_with()
    inject.assert_called_once_with()


@pytest.mark.parametrize("options", [{"master": True, "lazy-apps": True}, {"master": True, "lazy": True}])
def test_defer_injection_for_lazy_uwsgi_master(monkeypatch, options):
    uwsgi = ModuleType("uwsgi")
    uwsgi.masterpid = lambda: 123
    uwsgi.opt = options
    uwsgi.numproc = 1
    uwsgi.worker_id = lambda: 0
    monkeypatch.setitem(sys.modules, "uwsgi", uwsgi)
    _install_decorators(monkeypatch)
    inject = MagicMock()

    assert _load_helper().defer_injection(inject) is True
    inject.assert_not_called()

    uwsgi.post_fork_hook()
    inject.assert_called_once_with()


def test_defer_injection_until_uwsgi_module_is_available(monkeypatch):
    monkeypatch.delitem(sys.modules, "uwsgi", raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/venv/bin/python")
    monkeypatch.setattr(sys, "argv", ["/opt/venv/bin/python"])
    monkeypatch.setattr(os, "readlink", lambda path: "/usr/bin/uwsgi")
    monkeypatch.setattr(sys, "gettrace", lambda: None)
    settrace = MagicMock()
    monkeypatch.setattr(sys, "settrace", settrace)
    inject = MagicMock()

    helper = _load_helper()
    assert helper.defer_injection(inject) is True
    trace = settrace.call_args.args[0]
    inject.assert_not_called()

    uwsgi = ModuleType("uwsgi")
    uwsgi.masterpid = lambda: 123
    monkeypatch.setitem(sys.modules, "uwsgi", uwsgi)
    _install_decorators(monkeypatch)
    trace(None, "call", None)

    settrace.assert_called_with(None)
    inject.assert_not_called()

    uwsgi.post_fork_hook()
    inject.assert_called_once_with()


def test_defer_injection_preserves_existing_trace_function(monkeypatch):
    monkeypatch.delitem(sys.modules, "uwsgi", raising=False)
    monkeypatch.setattr(os, "readlink", lambda path: "/usr/bin/uwsgi")
    existing_trace = MagicMock()
    monkeypatch.setattr(sys, "gettrace", lambda: existing_trace)
    settrace = MagicMock()
    monkeypatch.setattr(sys, "settrace", settrace)
    monkeypatch.setattr(sys, "getprofile", lambda: None)
    setprofile = MagicMock()
    monkeypatch.setattr(sys, "setprofile", setprofile)
    inject = MagicMock()

    helper = _load_helper()
    assert helper.defer_injection(inject) is True
    profile = setprofile.call_args.args[0]
    settrace.assert_not_called()

    uwsgi = ModuleType("uwsgi")
    uwsgi.masterpid = lambda: 123
    monkeypatch.setitem(sys.modules, "uwsgi", uwsgi)
    _install_decorators(monkeypatch)
    profile(None, "call", None)

    setprofile.assert_called_with(None)
    assert sys.gettrace() is existing_trace
    inject.assert_not_called()

    uwsgi.post_fork_hook()
    inject.assert_called_once_with()


def test_defer_injection_preserves_noncallable_profile(monkeypatch):
    monkeypatch.delitem(sys.modules, "uwsgi", raising=False)
    monkeypatch.setattr(sys, "executable", "/usr/bin/uwsgi")
    monkeypatch.setattr(os, "readlink", lambda path: "/usr/bin/uwsgi")
    monkeypatch.setattr(sys, "gettrace", lambda: None)
    monkeypatch.setattr(sys, "settrace", MagicMock())
    profile = object()
    monkeypatch.setattr(sys, "getprofile", lambda: profile)
    setprofile = MagicMock()
    monkeypatch.setattr(sys, "setprofile", setprofile)
    inject = MagicMock()

    assert _load_helper().defer_injection(inject) is True
    assert sys.getprofile() is profile
    setprofile.assert_not_called()

    inject.assert_not_called()
