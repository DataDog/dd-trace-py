import os

import bm


class DjangoSimple(bm.Scenario):
    tracer_enabled: bool
    profiler_enabled: bool
    appsec_enabled: bool
    iast_enabled: bool
    span_code_origin_enabled: bool
    exception_replay_enabled: bool
    path: str

    def run(self):
        os.environ["DJANGO_SETTINGS_MODULE"] = "app"

        if self.profiler_enabled:
            os.environ.update(
                {"DD_PROFILING_ENABLED": "1", "DD_PROFILING_API_TIMEOUT": "0.1", "DD_PROFILING_UPLOAD_INTERVAL": "10"}
            )
        if self.appsec_enabled:
            os.environ.update({"DD_APPSEC_ENABLED ": "1"})
        if self.iast_enabled:
            os.environ.update({"DD_IAST_ENABLED ": "1"})
        if self.span_code_origin_enabled:
            os.environ.update({"DD_CODE_ORIGIN_FOR_SPANS_ENABLED": "1"})
        if self.exception_replay_enabled:
            os.environ.update({"DD_EXCEPTION_REPLAY_ENABLED": "1"})

        # This will not work with gevent workers as the gevent hub has not been
        # initialized when this hook is called.
        if self.tracer_enabled:
            import ddtrace.auto  # noqa:F401

        # If profiling enabled but not tracer than only run auto script for profiler
        if self.profiler_enabled and not self.tracer_enabled:
            import ddtrace.profiling.auto  # noqa:F401

        import django
        from django.test import Client

        django.setup()
        client = Client()

        def _(loops):
            for _ in range(loops):
                client.get(self.path)

        yield _
