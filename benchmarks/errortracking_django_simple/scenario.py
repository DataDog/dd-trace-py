import os

import bm


class DjangoSimple(bm.Scenario):
    tracer_enabled: bool
    errortracking_enabled: str
    path: str

    def run(self):
        os.environ["DJANGO_SETTINGS_MODULE"] = "app"

        if self.errortracking_enabled:
            os.environ.update({"DD_ERROR_TRACKING_HANDLED_ERRORS": self.errortracking_enabled})

        # This will not work with gevent workers as the gevent hub has not been
        # initialized when this hook is called.
        if self.tracer_enabled:
            import ddtrace.auto  # noqa:F401

        import django
        from django.test import Client

        django.setup()
        client = Client()

        def _(loops):
            for _ in range(loops):
                client.get(self.path)

        yield _
