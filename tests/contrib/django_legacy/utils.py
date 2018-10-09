import django
from django.test import TestCase
from django.db import connections
from functools import wraps
from ddtrace.tracer import Tracer
from ddtrace.contrib.django.conf import settings
from ddtrace.contrib.django.legacy import patch
from ddtrace.contrib.django.db import patch_db, unpatch_db
from ddtrace.contrib.django.cache import unpatch_cache
from ddtrace.contrib.django.legacy.patch import DD_DJANGO_PATCHED_FLAG
from ddtrace.contrib.django.templates import unpatch_template
from ddtrace.contrib.django.middleware import remove_exception_middleware, remove_trace_middleware
from ddtrace.contrib.django.legacy import patch as _ready

# testing
from ...test_tracer import DummyWriter


# testing tracer
tracer = Tracer()
tracer.writer = DummyWriter()


class DjangoTraceTestCase(TestCase):
    """
    Base class that provides an internal tracer according to given
    Datadog settings. This class ensures that the tracer spans are
    properly reset after each run. The tracer is available in
    the ``self.tracer`` attribute.
    """
    def setUp(self):
        self.unpatch()
        # assign the default tracer
        self.tracer = settings.TRACER
        # empty the tracer spans from previous operations
        # such as database creation queries
        self.tracer.writer.spans = []
        self.tracer.writer.pop_traces()

    def tearDown(self):
        # empty the tracer spans from test operations
        self.tracer.writer.spans = []
        self.tracer.writer.pop_traces()
        self.unpatch()

    def patch(self):
        patch()

    def activate_db_patch_for_non_request_based_tests(self):
        # Because of the way we patch, we have to manually trigger the method that we wrap,
        # otherwise when we do something like `User.objects.count()` the all() function that we wrap is not called and
        # the connection is accessed directly.
        # In the normal django request serving flow, the same effect is reached through the
        # signals.request_started.send(...) in the wsgi handler, but tests NOT based on request (e.g. test_connection)
        # would not have this activated
        connections.all()

    def unpatch(self):
        remove_trace_middleware()
        remove_exception_middleware()
        unpatch_cache()
        unpatch_db()
        unpatch_template()
        if hasattr(django, DD_DJANGO_PATCHED_FLAG):
            delattr(django, DD_DJANGO_PATCHED_FLAG)


class override_ddtrace_settings(object):
    def __init__(self, *args, **kwargs):
        self.items = list(kwargs.items())

    def unpatch_all(self):
        unpatch_cache()
        unpatch_db()
        unpatch_template()
        remove_trace_middleware()
        remove_exception_middleware()

    def __enter__(self):
        self.enable()

    def __exit__(self, exc_type, exc_value, traceback):
        self.disable()

    def enable(self):
        self.backup = {}
        for name, value in self.items:
            self.backup[name] = getattr(settings, name)
            setattr(settings, name, value)
        self.unpatch_all()
        _ready()

    def disable(self):
        for name, value in self.items:
            setattr(settings, name, self.backup[name])
        self.unpatch_all()
        remove_exception_middleware()
        _ready()

    def __call__(self, func):
        @wraps(func)
        def inner(*args, **kwargs):
            with(self):
                return func(*args, **kwargs)
        return inner
