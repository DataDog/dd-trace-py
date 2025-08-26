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
    django_instrument_middleware: bool
    django_instrument_caches: bool
    django_instrument_databases: bool
    always_create_database_spans: bool
    django_instrument_templates: bool

    def run(self):
        os.environ["DJANGO_SETTINGS_MODULE"] = "app"
        os.environ["DD_DJANGO_INSTRUMENT_MIDDLEWARE"] = "1" if self.django_instrument_middleware else "0"
        os.environ["DD_DJANGO_INSTRUMENT_CACHES"] = "1" if self.django_instrument_caches else "0"
        os.environ["DD_DJANGO_INSTRUMENT_DATABASES"] = "1" if self.django_instrument_databases else "0"
        os.environ["DD_DJANGO_ALWAYS_CREATE_DATABASE_SPANS"] = "1" if self.always_create_database_spans else "0"
        os.environ["DD_DJANGO_INSTRUMENT_TEMPLATES"] = "1" if self.django_instrument_templates else "0"

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

        # Try to clear any caches
        # DEV: Some of these methods/caches only exist in some versions of the library
        if self.django_instrument_databases:
            try:
                from ddtrace.contrib.internal.django import database

                try:
                    database.get_conn_config.invalidate()
                except Exception:
                    pass

                try:
                    database.get_service_name.invalidate()
                except Exception:
                    pass

                try:
                    database.get_conn_service_name.invalidate()
                except Exception:
                    pass
            except Exception:
                pass

        if self.django_instrument_caches:
            try:
                from ddtrace.contrib.internal.django import cache

                try:
                    cache.get_service_name.invalidate()
                except Exception:
                    pass

                try:
                    cache.func_cache_operation.invalidate()
                except Exception:
                    pass
            except Exception:
                pass
