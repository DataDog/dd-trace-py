import builtins
import contextlib
import copy
import pathlib
import types
from unittest import mock

import pytest
from wrapt import FunctionWrapper

import ddtrace.appsec._common_module_patches as cmp
from ddtrace.appsec._common_module_patches import _RaspContext
from ddtrace.appsec._common_module_patches import _ScopedRaspContext
from ddtrace.appsec._common_module_patches import _SsrfOpenerDirectorOpen
from ddtrace.appsec._common_module_patches import _SsrfUrllib3MakeRequest
from ddtrace.appsec._common_module_patches import _SsrfUrllib3Urlopen
from ddtrace.appsec._common_module_patches import patch_common_modules
from ddtrace.appsec._common_module_patches import unpatch_common_modules
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._patch_utils import try_unwrap
from ddtrace.appsec._patch_utils import try_wrap_function_wrapper
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.compat import is_at_least_py
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.wrapping.context import WrappingContext
from ddtrace.internal.wrapping.hooks import _MODULE_HOOKS
from ddtrace.internal.wrapping.hooks import _WRAPPING_CONTEXTS
from ddtrace.internal.wrapping.hooks import try_unwrap_context
from ddtrace.internal.wrapping.hooks import try_wrap_context
from tests.appsec.utils import blocking_waf_result


def test_patch_read():
    unpatch_common_modules()
    copy_open = copy.deepcopy(open)

    assert copy_open is open
    assert type(open) is types.BuiltinFunctionType
    assert not isinstance(open, FunctionWrapper)
    assert not isinstance(copy_open, FunctionWrapper)
    assert isinstance(open, types.BuiltinFunctionType)


def test_patch_read_enabled():
    unpatch_common_modules()
    original_open = open
    try:
        patch_common_modules()
        copy_open = copy.deepcopy(open)

        assert type(open) is FunctionWrapper
        assert isinstance(copy_open, FunctionWrapper)
        assert isinstance(open, FunctionWrapper)
        assert hasattr(open, "__wrapped__")
        assert open.__wrapped__ is original_open
    finally:
        unpatch_common_modules()


def test_patch_common_modules_unregisters_module_hooks():
    unpatch_common_modules()
    watchdog = ModuleWatchdog._instance
    assert watchdog is not None
    initial_hooks = {module: tuple(hooks) for module, hooks in watchdog._hook_map.items() if hooks}

    try:
        patch_common_modules()
        patched_hooks = sum(len(hooks) for hooks in watchdog._hook_map.values())
        assert patched_hooks > sum(len(hooks) for hooks in initial_hooks.values())

        patch_common_modules()
        assert sum(len(hooks) for hooks in watchdog._hook_map.values()) == patched_hooks
    finally:
        unpatch_common_modules()

    remaining_hooks = {module: tuple(hooks) for module, hooks in watchdog._hook_map.items() if hooks}
    assert remaining_hooks == initial_hooks


def test_opener_director_open_is_wrapped_with_a_context():
    """The urllib.request hook is a wrapping context, not a wrapt wrapper."""
    unpatch_common_modules()
    import urllib.request

    try:
        patch_common_modules()
        assert _SsrfOpenerDirectorOpen.is_wrapped(urllib.request.OpenerDirector.open)
        # No wrapt wrapper is installed on the attribute.
        assert not isinstance(urllib.request.OpenerDirector.open, FunctionWrapper)

        # Re-patching must stay a no-op rather than registering the context twice.
        patch_common_modules()
        assert _SsrfOpenerDirectorOpen.is_wrapped(urllib.request.OpenerDirector.open)
    finally:
        unpatch_common_modules()

    assert not _SsrfOpenerDirectorOpen.is_wrapped(urllib.request.OpenerDirector.open)


def test_opener_director_open_traceback_has_no_ddtrace_frames():
    """A connection error passing through the SSRF hook must not be attributed to ddtrace.

    A wrapt wrapper leaves its own frame in the traceback of every ordinary application
    error, which makes crash intake blame Datadog for customer bugs.
    """
    unpatch_common_modules()
    import traceback
    import urllib.request

    try:
        patch_common_modules()
        with pytest.raises(Exception) as raised:
            # Nothing listens on port 1, so this fails inside urllib, below our hook.
            urllib.request.urlopen("http://127.0.0.1:1/", timeout=1)

        frames = traceback.extract_tb(raised.value.__traceback__)
        ddtrace_frames = [frame.filename for frame in frames if "ddtrace" in frame.filename]
        assert not ddtrace_frames, ddtrace_frames
        assert any(frame.filename == __file__ for frame in frames)
    finally:
        unpatch_common_modules()


def test_opener_director_open_reads_fullurl_by_name():
    """The context reads the target's argument by name, replacing the old args/kwargs juggling.

    This is the riskiest part of the wrapt -> WrappingContext move: get the name wrong and RASP
    silently stops inspecting outgoing requests.
    """
    unpatch_common_modules()
    import urllib.request

    seen = []

    class _Recorder(_SsrfOpenerDirectorOpen):
        def __enter__(self):
            seen.append(self._locals().get("fullurl"))
            return super().__enter__()

    try:
        patch_common_modules()
        context = _SsrfOpenerDirectorOpen.extract(urllib.request.OpenerDirector.open)
        context.unwrap()
        _Recorder(urllib.request.OpenerDirector.open).wrap()

        with pytest.raises(Exception):
            urllib.request.urlopen("http://127.0.0.1:1/probe", timeout=1)
    finally:
        unpatch_common_modules()
        with contextlib.suppress(ValueError):
            _Recorder.extract(urllib.request.OpenerDirector.open).unwrap()

    assert seen == ["http://127.0.0.1:1/probe"]


@pytest.mark.parametrize("path", ["return", "error"])
def test_rasp_context_releases_its_core_context(path):
    """The core context opened in __enter__ must be released on the return and the error path.

    A leak would pin the context, and its full_url item, for the rest of the request.
    """
    import urllib.request

    context = _SsrfOpenerDirectorOpen(urllib.request.OpenerDirector.open)
    # Drive the lifecycle directly: RASP is inactive in this suite, so __enter__ would return
    # before opening a core context and the release paths would never be reached.
    _ScopedRaspContext.__enter__(context)
    context.set("use_body", False)
    context._open_core_context("url_open_analysis", full_url="http://127.0.0.1:1/", use_body=False)
    assert core.find_item("full_url") == "http://127.0.0.1:1/"

    if path == "return":
        # A plain object is not an HTTPResponse, so no WAF call is attempted.
        context.__return__(object())
    else:
        context.__exit__(ValueError, ValueError("boom"), None)

    assert core.find_item("full_url") is None


def test_blocking_exception_is_not_exception_derived():
    """RASP blocking depends on this and nothing else would catch a change.

    A registered context's __enter__ runs inside `except Exception: continue`, so a block deriving
    from Exception would be swallowed and the request would proceed unblocked.
    """
    assert issubclass(BlockingException, BaseException)
    assert not issubclass(BlockingException, Exception)


@pytest.mark.skipif(not is_at_least_py(3, 11), reason="Pre-3.11 wrapping uses different failed-entry cleanup semantics")
def test_blocked_recursive_call_restores_the_outer_call_storage():
    """A block during a recursive call's entry must only unwind that call.

    The contexts it already entered exit, and the outer call keeps its own storage.
    """
    from ddtrace.internal.wrapping.context import _UniversalWrappingContext

    exited = []

    class _Entered(WrappingContext):
        __priority__ = -1

        def __enter__(self):
            super().__enter__()
            self.set("value", self.get_local("value"))
            return self

        def __exit__(self, *exc):
            exited.append(self.get("value"))
            super().__exit__(*exc)

    class _Blocker(WrappingContext):
        def __enter__(self):
            super().__enter__()
            if self.get_local("value") == "inner":
                raise BlockingException("blocked")
            return self

    def target(value):
        if value == "outer":
            outer_storage = [context._storage.get() for context in (entered, blocker, universal)]
            with pytest.raises(BlockingException):
                target("inner")
            for context, storage in zip((entered, blocker, universal), outer_storage):
                assert context._storage.get() is storage
            assert entered.get("value") == "outer"
        return value

    entered = _Entered(target)
    blocker = _Blocker(target)
    entered.wrap()
    blocker.wrap()
    universal = _UniversalWrappingContext.extract(target)
    try:
        assert target("outer") == "outer"
        assert exited == ["inner"]
        assert all(context._storage.get() is None for context in (entered, blocker, universal))
    finally:
        blocker.unwrap()
        entered.unwrap()


def test_a_failing_rasp_hook_does_not_break_the_request():
    """A bug inside the hook must not reach the customer: the request proceeds and fails on its own."""
    unpatch_common_modules()
    import urllib.request

    try:
        patch_common_modules()
        with mock.patch.object(cmp, "get_rasp_capability", side_effect=RuntimeError("boom")):
            with pytest.raises(Exception) as raised:
                urllib.request.urlopen("http://127.0.0.1:1/", timeout=1)

        assert not isinstance(raised.value, RuntimeError)
        assert isinstance(raised.value, OSError)
    finally:
        unpatch_common_modules()


def test_try_wrap_context_survives_a_wrap_failure():
    """Bytecode rewriting can fail on shapes the bytecode library cannot round-trip.

    Losing a RASP hook is acceptable; breaking the customer's patching is not.
    """
    key = ("http.client", "HTTPConnection.putrequest")

    class _Unwrappable(WrappingContext):
        def wrap(self):
            raise RuntimeError("bytecode cannot round-trip this")

    try:
        # http.client is already imported, so the module hook fires immediately.
        try_wrap_context(key[0], key[1], _Unwrappable)
        assert key not in _WRAPPING_CONTEXTS
    finally:
        try_unwrap_context(key[0], key[1])


def test_a_failing_enter_does_not_strand_the_core_context():
    """__enter__ must not raise, or the context is dropped from the universal entered list.

    Neither __return__ nor __exit__ would then run, so an opened core context would never be
    released and every later core.set_item on that thread would land in the orphan.
    """
    import urllib.request

    class _BrokenAfterOpen(_SsrfOpenerDirectorOpen):
        def _handle_enter(self):
            self._open_core_context("url_open_analysis", full_url="http://127.0.0.1:1/", use_body=False)
            raise RuntimeError("bug after the core context is open")

    context = _BrokenAfterOpen(urllib.request.OpenerDirector.open)
    _ScopedRaspContext.__enter__(context)
    context.__enter__()

    assert core.find_item("full_url") is None


def test_a_failing_return_does_not_reach_the_universal_context():
    """A raising __return__ sets the skip flag and strands this call's storage (APPSEC-69961).

    Swallowing here, as the debugger and selenium contexts do, also keeps a bug in the response
    analysis from surfacing to the customer.
    """
    import urllib.request

    class HTTPResponse:
        status = 200

        def getheaders(self):
            raise RuntimeError("bug in the response analysis")

    context = _SsrfOpenerDirectorOpen(urllib.request.OpenerDirector.open)
    _ScopedRaspContext.__enter__(context)
    context.set("use_body", False)
    context._open_core_context("url_open_analysis", full_url="http://127.0.0.1:1/", use_body=False)

    # Must not raise, and must still release the core context.
    context.__return__(HTTPResponse())
    assert core.find_item("full_url") is None


def test_try_wrap_context_rebinds_after_a_module_reload():
    """A reload gives the class new function objects, and those must get wrapped too.

    Keying only on (module, name) treated the key as proof the current target was wrapped, so the
    reloaded methods ran without RASP until a full unpatch/patch cycle.
    """
    import http.client

    key = ("http.client", "HTTPConnection.putrequest")
    original = http.client.HTTPConnection.__dict__["putrequest"]

    class _Ctx(WrappingContext):
        pass

    def reloaded(self, method, url, skip_host=False, skip_accept_encoding=False):
        return None

    try:
        try_wrap_context(key[0], key[1], _Ctx)
        first = _WRAPPING_CONTEXTS[key]
        assert first.__wrapped__ is original

        # Stand in for a reload: the attribute now holds a different function object.
        http.client.HTTPConnection.putrequest = reloaded
        for hook in _MODULE_HOOKS[key]:
            hook(http.client)

        rebound = _WRAPPING_CONTEXTS[key]
        assert rebound is not first
        assert rebound.__wrapped__ is reloaded
    finally:
        try_unwrap_context(key[0], key[1])
        http.client.HTTPConnection.putrequest = original


def _entered_context():
    """A _SsrfOpenerDirectorOpen with its core context open, without needing RASP enabled."""
    import urllib.request

    context = _SsrfOpenerDirectorOpen(urllib.request.OpenerDirector.open)
    _ScopedRaspContext.__enter__(context)
    context.set("use_body", False)
    context._open_core_context("url_open_analysis", full_url="http://127.0.0.1:1/", use_body=False)
    return context


def test_opener_director_open_reports_the_downstream_response():
    """The API10 down-response WAF call moved from inside a `with` block to __return__."""

    class HTTPResponse:  # production matches on the class name
        status = 200

        def getheaders(self):
            return [("Content-Type", "application/json")]

    context = _entered_context()
    with mock.patch.object(cmp, "call_waf_callback") as call_waf:
        context.__return__(HTTPResponse())

    call_waf.assert_called_once_with(
        {"DOWN_RES_STATUS": "200", "DOWN_RES_HEADERS": {"Content-Type": "application/json"}},
        rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
    )


def test_opener_director_open_reports_a_downstream_http_error():
    """Same for the error path, which moved to __exit__."""

    class HTTPError(Exception):  # production matches on the class name
        code = 404
        headers = {"Content-Type": "text/html"}

    context = _entered_context()
    with mock.patch.object(cmp, "call_waf_callback") as call_waf:
        context.__exit__(HTTPError, HTTPError(), None)

    call_waf.assert_called_once_with(
        {"DOWN_RES_STATUS": "404", "DOWN_RES_HEADERS": {"Content-Type": "text/html"}},
        rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
    )


def test_opener_director_open_skips_a_redirect_response():
    """3xx is handled deeper, in getresponse, so __return__ must not double-call the WAF."""

    class HTTPResponse:
        status = 302

        def getheaders(self):
            return []

    context = _entered_context()
    with mock.patch.object(cmp, "call_waf_callback") as call_waf:
        context.__return__(HTTPResponse())

    call_waf.assert_not_called()


@pytest.mark.parametrize(
    "builtin_function_name",
    [
        "all",
        "any",
        "ascii",
        "bin",
        "bool",
        "breakpoint",
        "bytearray",
        "bytes",
        "callable",
        "chr",
        "classmethod",
        "compile",
        "complex",
        "copyright",
        "credits",
        "delattr",
        "dict",
        "dir",
        "divmod",
        "enumerate",
        "eval",
        "exec",
        "exit",
        "filter",
        "float",
        "format",
        "frozenset",
        "getattr",
        "globals",
        "hasattr",
        "hash",
        "help",
        "hex",
        "id",
        "input",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "license",
        "list",
        "locals",
        "map",
        "max",
        "memoryview",
        "min",
        "next",
        "object",
        "oct",
        "open",
        "ord",
        "pow",
        "print",
        "property",
        "quit",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "setattr",
        "slice",
        "sorted",
        "staticmethod",
        "str",
        "sum",
        "super",
        "tuple",
        "vars",
        "zip",
    ],
)
def test_other_builtin_functions(builtin_function_name):
    def dummywrapper(callable, instance, args, kwargs):  # noqa: A002
        return callable(*args, **kwargs)

    try:
        try_wrap_function_wrapper("builtins", builtin_function_name, dummywrapper)

        original_func = getattr(builtins, builtin_function_name)
        copy_func = copy.deepcopy(original_func)

        assert type(original_func) is FunctionWrapper
        assert isinstance(copy_func, FunctionWrapper)
        assert isinstance(original_func, FunctionWrapper)
        assert hasattr(original_func, "__wrapped__")
    finally:
        try_unwrap("builtins", builtin_function_name)


def test_urllib3_poolmanager_redirect_inspects_absolute_target():
    """The URL handed to the downstream hook must be the absolute redirected target.

    PoolManager calls HTTPConnectionPool.urlopen with the relative URI, so a naive read stores a
    value with no host at all. Drives a real redirect rather than mocking the hop.
    """
    urllib3 = pytest.importorskip("urllib3")
    from http.server import BaseHTTPRequestHandler
    from http.server import ThreadingHTTPServer
    import threading

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/source":
                self.send_response(302)
                self.send_header("Location", "/target")
            else:
                self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args, **kwargs):
            pass  # silence test output

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Stand in for the real _make_request hook: record the inspected URL then release it, so the
    # set/discard flow across redirects is faithful. Drives the shipped urlopen context above it.
    inspected = []

    class _MakeRequestRecorder(_RaspContext):
        def __enter__(self):
            super().__enter__()
            inspected.append(core.find_item("full_url"))
            core.discard_item("full_url")
            return self

    core.discard_item("full_url")
    try_wrap_context("urllib3.connectionpool", "HTTPConnectionPool.urlopen", _SsrfUrllib3Urlopen)
    try_wrap_context("urllib3.connectionpool", "HTTPConnectionPool._make_request", _MakeRequestRecorder)
    try:
        # The context gates on the capability, where the old wrapt wrapper published the URL
        # unconditionally, so this has to be on for the URL to be published at all.
        with mock.patch.object(cmp, "get_rasp_capability", return_value=True):
            pool_manager = urllib3.PoolManager(num_pools=1)
            try:
                response = pool_manager.request("GET", f"http://127.0.0.1:{port}/source", timeout=10)
                assert response.status == 200
            finally:
                pool_manager.clear()
    finally:
        try_unwrap_context("urllib3.connectionpool", "HTTPConnectionPool.urlopen")
        try_unwrap_context("urllib3.connectionpool", "HTTPConnectionPool._make_request")
        core.discard_item("full_url")
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert inspected, "no downstream request was inspected"
    # The redirected hop must be inspected as an absolute URL carrying the target host.
    assert inspected[-1] == f"http://127.0.0.1:{port}/target", inspected


def test_exit_after_return_does_not_raise():
    """__return__ pops this call's storage, so a later __exit__ finds none.

    The core-context helpers went through the strict BaseWrappingContext.get, and in __exit__ the
    _rasp_active() call sits outside the try, so a TypeError escaped to the caller.
    """
    import urllib.request

    context = _SsrfOpenerDirectorOpen(urllib.request.OpenerDirector.open)
    _ScopedRaspContext.__enter__(context)
    context.__return__(object())

    context.__exit__(ValueError, ValueError("boom"), None)


def test_webbrowser_open_inspects_the_url_and_not_the_new_argument():
    """webbrowser.open is open(url, new=0, autoraise=True).

    The shared wrapt shim read the second positional argument, so RASP inspected `new` and the
    hook never had a URL at all. A block in __enter__ also keeps this test from launching a browser.
    """
    import webbrowser

    from ddtrace.appsec._contrib.webbrowser import patch as wb

    unpatch_common_modules()
    seen = []
    try:
        patch_common_modules()
        with (
            mock.patch.object(wb, "get_rasp_capability", return_value=True),
            mock.patch.object(wb, "get_active_asm_context", return_value=mock.Mock()),
            mock.patch.object(wb, "open_rasp_subcontext_scope"),
            mock.patch.object(wb, "get_blocked", return_value={"status_code": 403}),
            mock.patch.object(
                wb,
                "call_waf_callback",
                side_effect=lambda addresses=None, **kwargs: seen.append(addresses) or blocking_waf_result(),
            ),
        ):
            with pytest.raises(BlockingException) as raised:
                webbrowser.open("http://127.0.0.1:1/probe", 2)
    finally:
        unpatch_common_modules()

    assert seen == [{EXPLOIT_PREVENTION.ADDRESS.SSRF: "http://127.0.0.1:1/probe"}]
    assert raised.value.args[3] == "http://127.0.0.1:1/probe"


def test_webbrowser_open_is_wrapped_with_a_context_not_wrapt():
    """A wrapt wrapper would put a frame of ours in the traceback of any error passing through."""
    import webbrowser

    from ddtrace.appsec._contrib.webbrowser.patch import _SsrfWebbrowserOpen

    unpatch_common_modules()
    try:
        patch_common_modules()
        assert _SsrfWebbrowserOpen.is_wrapped(webbrowser.open)
        assert not isinstance(webbrowser.open, FunctionWrapper)

        # Re-patching must stay a no-op rather than registering the context twice.
        patch_common_modules()
        assert _SsrfWebbrowserOpen.is_wrapped(webbrowser.open)
    finally:
        unpatch_common_modules()

    assert not _SsrfWebbrowserOpen.is_wrapped(webbrowser.open)


def test_each_webbrowser_call_gets_its_own_rasp_subcontext():
    """open_rasp_subcontext_scope is reentrant, so it must be called per outgoing request.

    Without a per-call core context the first call creates the holder and every later call in the
    same request reuses it, so they are all grouped as one outgoing request.
    """
    import webbrowser

    from ddtrace.appsec._asm_request_context import _RASP_SUBCONTEXT
    from ddtrace.appsec._contrib.webbrowser import patch as wb

    unpatch_common_modules()
    holders = []
    try:
        patch_common_modules()
        with (
            mock.patch.object(wb, "get_rasp_capability", return_value=True),
            mock.patch.object(wb, "get_active_asm_context", return_value=mock.Mock()),
            mock.patch.object(
                wb,
                "call_waf_callback",
                side_effect=lambda addresses=None, **kwargs: holders.append(core.find_item(_RASP_SUBCONTEXT)),
            ),
        ):
            webbrowser.open("http://127.0.0.1:1/one")
            webbrowser.open("http://127.0.0.1:1/two")
    finally:
        unpatch_common_modules()

    assert len(holders) == 2, holders
    assert all(holder is not None for holder in holders)
    assert holders[0] is not holders[1], "both calls shared one subcontext"


def test_a_custom_installed_opener_still_publishes_the_url():
    """install_opener takes any object with an open method, so OpenerDirector.open can be bypassed.

    These hooks publish full_url for the http.client hook to inspect; without one on urlopen a
    custom opener leaves it unpublished and the downstream request goes uninspected.
    """
    import urllib.request

    seen = []

    class CustomOpener(urllib.request.OpenerDirector):
        def open(self, fullurl, data=None, timeout=None):
            seen.append(core.find_item("full_url"))
            raise OSError("stop here, the point is what was published")

    unpatch_common_modules()
    previous = urllib.request._opener
    try:
        patch_common_modules()
        urllib.request.install_opener(CustomOpener())
        with (
            mock.patch.object(cmp, "get_rasp_capability", return_value=True),
            mock.patch.object(cmp, "get_active_asm_context", return_value=mock.Mock(downstream_requests=0)),
        ):
            with pytest.raises(OSError):
                urllib.request.urlopen("http://127.0.0.1:1/custom")
    finally:
        urllib.request._opener = previous
        unpatch_common_modules()

    assert seen == ["http://127.0.0.1:1/custom"], seen


def test_urlopen_publishes_the_url_only_once():
    """Both hooks are installed, so the inner one must stand down when nested.

    Two nested url_open_analysis contexts would mean the outgoing request is inspected twice.
    """
    import urllib.request

    unpatch_common_modules()
    opened = []
    original = _ScopedRaspContext._open_core_context

    def recording_open(self, name, **kwargs):
        opened.append(kwargs.get("full_url"))
        return original(self, name, **kwargs)

    try:
        patch_common_modules()
        with (
            mock.patch.object(cmp, "get_rasp_capability", return_value=True),
            mock.patch.object(cmp, "get_active_asm_context", return_value=mock.Mock(downstream_requests=0)),
            mock.patch.object(_ScopedRaspContext, "_open_core_context", recording_open),
        ):
            with contextlib.suppress(Exception):
                urllib.request.urlopen("http://127.0.0.1:1/once", timeout=1)
    finally:
        unpatch_common_modules()

    assert opened == ["http://127.0.0.1:1/once"], opened


def test_a_rewriting_opener_publishes_the_url_it_actually_requests():
    """A custom opener may rewrite the URL before delegating to the base open.

    Deduplicating on "some enclosing scope published something" would suppress the inner call and
    leave the http.client hook inspecting the stale outer URL, letting the real destination pass.
    """
    import urllib.request

    published = []
    original = _ScopedRaspContext._open_core_context

    def recording_open(self, name, **kwargs):
        published.append(kwargs.get("full_url"))
        return original(self, name, **kwargs)

    class RewritingOpener(urllib.request.OpenerDirector):
        def open(self, fullurl, data=None, timeout=None):
            # Rewrite, then delegate to the instrumented base implementation.
            return urllib.request.OpenerDirector.open(self, "http://127.0.0.1:1/rewritten", data, timeout)

    unpatch_common_modules()
    previous = urllib.request._opener
    try:
        patch_common_modules()
        urllib.request.install_opener(RewritingOpener())
        with (
            mock.patch.object(cmp, "get_rasp_capability", return_value=True),
            mock.patch.object(cmp, "get_active_asm_context", return_value=mock.Mock(downstream_requests=0)),
            mock.patch.object(_ScopedRaspContext, "_open_core_context", recording_open),
        ):
            with contextlib.suppress(Exception):
                urllib.request.urlopen("http://127.0.0.1:1/original")
    finally:
        urllib.request._opener = previous
        unpatch_common_modules()

    assert "http://127.0.0.1:1/rewritten" in published, published


def test_a_redirect_publishes_the_redirected_url():
    """urllib follows a redirect by calling OpenerDirector.open again with the new URL.

    The deduplication must not suppress that hop: the redirect target is a different destination
    and has to be evaluated on its own. It works because the http.client hook discards full_url
    once it has consumed it, so the nested call no longer sees a matching URL.
    """
    from http.server import BaseHTTPRequestHandler
    from http.server import ThreadingHTTPServer
    import threading
    import urllib.request

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/source":
                self.send_response(302)
                self.send_header("Location", "/target")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    published = []
    original = _ScopedRaspContext._open_core_context

    def recording_open(self, name, **kwargs):
        published.append(kwargs.get("full_url"))
        return original(self, name, **kwargs)

    unpatch_common_modules()
    try:
        patch_common_modules()
        with (
            mock.patch.object(cmp, "get_rasp_capability", return_value=True),
            mock.patch.object(cmp, "get_active_asm_context", return_value=mock.Mock(downstream_requests=0)),
            mock.patch.object(cmp, "call_waf_callback", return_value=None),
            mock.patch.object(_ScopedRaspContext, "_open_core_context", recording_open),
        ):
            with contextlib.suppress(Exception):
                urllib.request.urlopen(f"http://127.0.0.1:{port}/source", timeout=5)
    finally:
        unpatch_common_modules()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert f"http://127.0.0.1:{port}/source" in published, published
    assert any(url and url.endswith("/target") for url in published), published


def _v1_make_request(self, conn, method, url, timeout=None, chunked=False, **httplib_request_kw):
    """urllib3 v1: body and headers arrive in the **kwargs bag, not as named parameters."""
    return "called"


def _v2_make_request(self, conn, method, url, body=None, headers=None, retries=None, chunked=False):
    """urllib3 v2: named parameters."""
    return "called"


@pytest.mark.parametrize("target", [_v1_make_request, _v2_make_request])
def test_arg_reads_body_and_headers_on_both_urllib3_versions(target):
    """v1 declares _make_request with **httplib_request_kw, so a plain locals read finds nothing.

    Getting this wrong means DOWN_REQ_HEADERS and DOWN_REQ_BODY are silently empty on urllib3 1.x,
    which is still in the CI matrix.
    """
    seen = []

    class _Probe(_RaspContext):
        def __enter__(self):
            super().__enter__()
            seen.append((self._arg("method"), self._arg("body"), self._arg("headers", {})))
            return self

    context = _Probe(target)
    context.wrap()
    try:
        target(None, None, "POST", "/p", body='{"a": 1}', headers={"Content-Type": "application/json"})
    finally:
        context.unwrap()

    assert seen == [("POST", '{"a": 1}', {"Content-Type": "application/json"})]


def test_urllib3_hooks_are_wrapping_contexts_not_wrapt():
    """A wrapt wrapper would leave its frame in the traceback of any urllib3 error passing through."""
    pytest.importorskip("urllib3")
    import urllib3.connectionpool

    unpatch_common_modules()
    try:
        patch_common_modules()
        pool = urllib3.connectionpool.HTTPConnectionPool
        assert _SsrfUrllib3Urlopen.is_wrapped(pool.urlopen)
        assert _SsrfUrllib3MakeRequest.is_wrapped(pool._make_request)
        assert not isinstance(pool.urlopen, FunctionWrapper)
        assert not isinstance(pool._make_request, FunctionWrapper)
    finally:
        unpatch_common_modules()

    assert not _SsrfUrllib3Urlopen.is_wrapped(urllib3.connectionpool.HTTPConnectionPool.urlopen)


def test_the_urllib3_contrib_no_longer_installs_the_appsec_wrappers():
    """Both used to register the same wrappers on the same targets.

    wrapt deduplicated that by identity; a wrapping context lives in the bytecode and cannot be
    seen by that guard, so leaving the contrib install in place would call the WAF twice.
    """
    pytest.importorskip("urllib3")
    import urllib3.connectionpool

    from ddtrace.contrib.internal.urllib3 import patch as urllib3_contrib

    unpatch_common_modules()
    try:
        patch_common_modules()
        urllib3_contrib.patch()

        # The contrib still owns its tracing wrapper on urlopen, over our context.
        assert _SsrfUrllib3MakeRequest.is_wrapped(urllib3.connectionpool.HTTPConnectionPool._make_request)
        assert not isinstance(urllib3.connectionpool.HTTPConnectionPool._make_request, FunctionWrapper)
    finally:
        with contextlib.suppress(Exception):
            urllib3_contrib.unpatch()
        unpatch_common_modules()

    assert "appsec" not in pathlib.Path(urllib3_contrib.__file__).read_text()


def test_urllib3_traceback_frames_come_only_from_the_tracing_contrib():
    """The RASP hooks leave no frame of their own; urllib3 APM tracing still does.

    urllib3 APM tracing is off by default, so the default configuration is clean. This pins
    which side owns the residual frame so the release note cannot overstate the fix.
    """
    pytest.importorskip("urllib3")
    import traceback

    import urllib3

    from ddtrace.contrib.internal.urllib3 import patch as urllib3_contrib

    def failing_request():
        # Nothing listens on port 1, so this fails below our hooks. retries=False keeps the
        # original traceback instead of urllib3 re-raising a MaxRetryError.
        pool = urllib3.PoolManager(num_pools=1, retries=False)
        try:
            with pytest.raises(Exception) as raised:
                pool.request("GET", "http://127.0.0.1:1/", timeout=1)
        finally:
            pool.clear()
        return traceback.extract_tb(raised.value.__traceback__)

    unpatch_common_modules()
    try:
        patch_common_modules()
        frames = failing_request()
        assert not [f.filename for f in frames if "ddtrace" in f.filename]

        urllib3_contrib.patch()
        ours = [f for f in failing_request() if "ddtrace" in f.filename]
        assert ours, "expected the tracing contrib to own a frame"
        assert all("appsec" not in f.filename for f in ours), [f.filename for f in ours]
        assert "_wrap_urlopen" in [f.name for f in ours], [(f.filename, f.name) for f in ours]
    finally:
        with contextlib.suppress(Exception):
            urllib3_contrib.unpatch()
        unpatch_common_modules()
