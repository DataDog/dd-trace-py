import builtins
import contextlib
import copy
import types

import pytest
from wrapt import FunctionWrapper

from ddtrace.appsec._common_module_patches import _RaspContext
from ddtrace.appsec._common_module_patches import _SsrfHttpConnectionGetresponse
from ddtrace.appsec._common_module_patches import _SsrfHttpConnectionRequest
from ddtrace.appsec._common_module_patches import _SsrfOpenerDirectorOpen
from ddtrace.appsec._common_module_patches import patch_common_modules
from ddtrace.appsec._common_module_patches import try_unwrap
from ddtrace.appsec._common_module_patches import try_wrap_function_wrapper
from ddtrace.appsec._common_module_patches import unpatch_common_modules
from ddtrace.appsec._common_module_patches import wrapped_urllib3_urlopen
from ddtrace.appsec._patch_utils import _DD_WRAPPING_CONTEXTS
from ddtrace.internal import core
from ddtrace.internal.module import ModuleWatchdog


def test_patch_read():
    unpatch_common_modules()
    copy_open = copy.deepcopy(open)

    assert copy_open is open
    assert type(open) == types.BuiltinFunctionType
    assert not isinstance(open, FunctionWrapper)
    assert not isinstance(copy_open, FunctionWrapper)
    assert isinstance(open, types.BuiltinFunctionType)


def test_patch_read_enabled():
    unpatch_common_modules()
    original_open = open
    try:
        patch_common_modules()
        copy_open = copy.deepcopy(open)

        assert type(open) == FunctionWrapper
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
            seen.append(self._arg("fullurl"))
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
    _RaspContext.__enter__(context)
    context.set("use_body", False)
    context._open_core_context("url_open_analysis", full_url="http://127.0.0.1:1/", use_body=False)
    assert core.find_item("full_url") == "http://127.0.0.1:1/"

    if path == "return":
        # A plain object is not an HTTPResponse, so no WAF call is attempted.
        context.__return__(object())
    else:
        context.__exit__(ValueError, ValueError("boom"), None)

    assert core.find_item("full_url") is None


@pytest.mark.parametrize("appsec_first", [True, False])
def test_http_client_context_coexists_with_httplib_contrib(appsec_first):
    """appsec bytecode-wraps the same HTTPConnection attributes that the httplib integration
    wrapt-wraps, so both mechanisms must survive either patch order.
    """
    unpatch_common_modules()
    import http.client

    from ddtrace.contrib.internal.httplib.patch import patch as httplib_patch
    from ddtrace.contrib.internal.httplib.patch import unpatch as httplib_unpatch

    httplib_unpatch()
    try:
        if appsec_first:
            patch_common_modules()
            httplib_patch()
        else:
            httplib_patch()
            patch_common_modules()

        request_ctx = _DD_WRAPPING_CONTEXTS.get(("http.client", "HTTPConnection.request"))
        assert isinstance(request_ctx, _SsrfHttpConnectionRequest)
        assert isinstance(
            _DD_WRAPPING_CONTEXTS.get(("http.client", "HTTPConnection.getresponse")),
            _SsrfHttpConnectionGetresponse,
        )

        # Being registered is not enough: the hook must actually run under both orders, or
        # RASP silently stops inspecting downstream requests.
        entered = []

        class _Probe(_SsrfHttpConnectionRequest):
            def __enter__(self):
                result = super().__enter__()
                entered.append((self._arg("method"), self._arg("url")))
                return result

        request_fn = request_ctx.__wrapped__
        request_ctx.unwrap()
        probe = _Probe(request_fn)
        probe.wrap()

        # Both layers still deliver a working client: a refused connection must surface as
        # OSError, not as an instrumentation error.
        conn = http.client.HTTPConnection("127.0.0.1", 1, timeout=1)
        with pytest.raises(OSError):
            conn.request("GET", "/")

        assert entered == [("GET", "/")]
    finally:
        with contextlib.suppress(Exception):
            probe.unwrap()
        httplib_unpatch()
        unpatch_common_modules()


def _code_sizes():
    import http.client
    import urllib.request

    return {
        "request": len(http.client.HTTPConnection.__dict__["request"].__code__.co_code),
        "getresponse": len(http.client.HTTPConnection.__dict__["getresponse"].__code__.co_code),
        "open": len(urllib.request.OpenerDirector.__dict__["open"].__code__.co_code),
    }


def test_context_unpatch_restores_the_original_bytecode():
    """Unpatching must really release the function, whatever else is wrapped on the attribute.

    Resolving the attribute at unwrap time returns a contrib wrapt proxy rather than the object
    that was wrapped, so unwrap silently no-ops and leaves the rewritten code in place. The next
    patch then rewrites on top of it, growing the code object every cycle until the bytecode
    library can no longer parse it, which surfaces far away as a KeyError from an unrelated test.
    """
    from ddtrace.contrib.internal.httplib.patch import patch as httplib_patch
    from ddtrace.contrib.internal.httplib.patch import unpatch as httplib_unpatch

    unpatch_common_modules()
    httplib_unpatch()
    baseline = _code_sizes()

    try:
        for cycle in range(6):
            # Alternate the order so both interleavings with the httplib integration are covered.
            if cycle % 2:
                httplib_patch()
                patch_common_modules()
            else:
                patch_common_modules()
                httplib_patch()
            httplib_unpatch()
            unpatch_common_modules()
            assert _code_sizes() == baseline, f"bytecode grew on cycle {cycle}"
    finally:
        httplib_unpatch()
        unpatch_common_modules()


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

        assert type(original_func) == FunctionWrapper
        assert isinstance(copy_func, FunctionWrapper)
        assert isinstance(original_func, FunctionWrapper)
        assert hasattr(original_func, "__wrapped__")
    finally:
        try_unwrap("builtins", builtin_function_name)


def test_urllib3_poolmanager_redirect_inspects_absolute_target():
    """Functional regression test for APPSEC-68569: drive a real urllib3 PoolManager redirect and
    assert the URL handed to the downstream SSRF/API10 wrapper is the absolute redirected target.

    PoolManager calls ``HTTPConnectionPool.urlopen(method, request_uri, ...)`` with the *relative*
    URI, so the buggy wrapper stored the body/``None`` (no host); the fix rebuilds the absolute URL.
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

    # Stand in for HTTPConnectionPool._make_request: record the inspected URL then release it,
    # exactly as the real RASP wrapper does, so the set/discard flow across redirects is faithful.
    inspected = []

    def _make_request_recorder(func, instance, args, kwargs):
        inspected.append(core.find_item("full_url"))
        core.discard_item("full_url")
        return func(*args, **kwargs)

    core.discard_item("full_url")
    try_wrap_function_wrapper("urllib3.connectionpool", "HTTPConnectionPool.urlopen", wrapped_urllib3_urlopen)
    try_wrap_function_wrapper("urllib3.connectionpool", "HTTPConnectionPool._make_request", _make_request_recorder)
    try:
        pool_manager = urllib3.PoolManager(num_pools=1)
        try:
            response = pool_manager.request("GET", "http://127.0.0.1:{}/source".format(port), timeout=10)
            assert response.status == 200
        finally:
            pool_manager.clear()
    finally:
        try_unwrap("urllib3.connectionpool", "HTTPConnectionPool.urlopen")
        try_unwrap("urllib3.connectionpool", "HTTPConnectionPool._make_request")
        core.discard_item("full_url")
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert inspected, "no downstream request was inspected"
    # The redirected hop must be inspected as an absolute URL carrying the target host.
    assert inspected[-1] == "http://127.0.0.1:{}/target".format(port), inspected
