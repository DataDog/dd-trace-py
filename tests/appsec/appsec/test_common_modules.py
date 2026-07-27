import builtins
import copy
import types

import pytest
from wrapt import FunctionWrapper

from ddtrace.appsec._common_module_patches import patch_common_modules
from ddtrace.appsec._common_module_patches import try_unwrap
from ddtrace.appsec._common_module_patches import try_wrap_function_wrapper
from ddtrace.appsec._common_module_patches import unpatch_common_modules
from ddtrace.appsec._common_module_patches import wrapped_urllib3_urlopen
from ddtrace.internal import core


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
