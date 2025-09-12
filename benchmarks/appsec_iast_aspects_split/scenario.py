import bm
from bm.utils import override_env


try:
    # 3.15+
    from ddtrace.appsec._iast._iast_request_context_base import _iast_finish_request as end_iast_context
    from ddtrace.appsec._iast._iast_request_context_base import _start_iast_context as iast_start_request
    from ddtrace.appsec._iast._iast_request_context_base import set_iast_request_enabled
except ImportError:
    try:
        # 3.6+
        from ddtrace.appsec._iast._iast_request_context_base import end_iast_context
        from ddtrace.appsec._iast._iast_request_context_base import iast_start_request
        from ddtrace.appsec._iast._iast_request_context_base import set_iast_request_enabled
    except ImportError:
        # Pre 3.6
        from ddtrace.appsec._iast._iast_request_context import end_iast_context
        from ddtrace.appsec._iast._iast_request_context import iast_start_request
        from ddtrace.appsec._iast._iast_request_context import set_iast_request_enabled


def _start_iast_context_and_oce():
    # oce.reconfigure()
    # oce.acquire_request(None)
    iast_start_request()
    set_iast_request_enabled(True)


def _end_iast_context_and_oce():
    end_iast_context()
    # oce.release_request()


with override_env({"DD_IAST_ENABLED": "True"}):
    import functions


class IASTAspectsSplit(bm.Scenario):
    iast_enabled: bool
    function_name: str

    def run(self):
        if self.iast_enabled:
            with override_env({"DD_IAST_ENABLED": "True"}):
                _start_iast_context_and_oce()

        def _(loops):
            for _ in range(loops):
                if self.iast_enabled:
                    with override_env({"DD_IAST_ENABLED": "True"}):
                        getattr(functions, self.function_name)()

                else:
                    getattr(functions, self.function_name)()

        yield _
        if self.iast_enabled:
            with override_env({"DD_IAST_ENABLED": "True"}):
                _end_iast_context_and_oce()
