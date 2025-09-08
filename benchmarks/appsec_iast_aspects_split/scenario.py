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
            from ddtrace.appsec._iast._iast_request_context_base import set_iast_request_enabled  # noqa: F401
            from ddtrace.appsec._iast._iast_request_context_base import start_iast_context  # noqa: F401
        except ImportError:
            # Pre 3.6
            try:
                from ddtrace.appsec._iast._iast_request_context import end_iast_context  # noqa: F401
                from ddtrace.appsec._iast._iast_request_context import set_iast_request_enabled  # noqa: F401
                from ddtrace.appsec._iast._iast_request_context import start_iast_context  # noqa: F401
            except ImportError:
                pass


with override_env({"DD_IAST_ENABLED": "True"}):
    import functions


class IASTAspectsSplit(bm.Scenario):
    iast_enabled: bool
    function_name: str

    def run(self):
        if self.iast_enabled:
            with override_env({"DD_IAST_ENABLED": "True"}):
                _iast_start_request()

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
                _iast_finish_request()
