import contextlib

import bm
from bm.utils import override_env


with override_env({"DD_IAST_ENABLED": "True"}):
    try:
        # 3.15+
        from ddtrace.appsec._iast._iast_request_context_base import _iast_finish_request
        from ddtrace.appsec._iast._iast_request_context_base import _iast_start_request
    except ImportError:
        try:
            # 3.6+
            from ddtrace.appsec._iast._iast_request_context_base import set_iast_request_enabled
            from ddtrace.appsec._iast._iast_request_context_base import start_iast_context
        except ImportError:
            # Pre 3.6
            try:
                from ddtrace.appsec._iast._iast_request_context import end_iast_context  # noqa: F401
                from ddtrace.appsec._iast._iast_request_context import set_iast_request_enabled  # noqa: F401
                from ddtrace.appsec._iast._iast_request_context import start_iast_context  # noqa: F401
            except ImportError:
                pass


@contextlib.contextmanager
def _with_iast_context():
    with override_env({"DD_IAST_ENABLED": "True"}):
        _iast_start_request()
        yield
        _iast_finish_request()


@contextlib.contextmanager
def _without_iast_context():
    yield


with override_env({"DD_IAST_ENABLED": "True"}):
    import functions


class IASTAspects(bm.Scenario):
    iast_enabled: bool
    function_name: str

    def run(self):
        def _(loops):
            for _ in range(loops):
                getattr(functions, self.function_name)()

        context = _with_iast_context if self.iast_enabled else _without_iast_context
        with context():
            yield _
