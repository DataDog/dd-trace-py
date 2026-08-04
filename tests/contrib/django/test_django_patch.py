import pytest

from ddtrace.contrib.internal.django.patch import get_version
from ddtrace.contrib.internal.django.patch import patch
from tests.contrib.patch import PatchTestCase


class TestDjangoPatch(PatchTestCase.Base):
    __integration_name__ = "django"
    __module_name__ = "django"
    __patch_func__ = patch
    __unpatch_func__ = None
    __get_version__ = get_version

    def assert_module_patched(self, django):
        import django.apps.registry

        self.assert_wrapped(django.apps.registry.Apps.populate)

        import django.core.handlers.base

        self.assert_wrapped(django.core.handlers.base.BaseHandler.load_middleware)
        self.assert_wrapped(django.core.handlers.base.BaseHandler.get_response)

        import django.template.base

        self.assert_not_wrapped(django.template.base.Template.render)
        if django.VERSION >= (2, 0, 0):
            self.assert_wrapped(django.urls.path)
            self.assert_wrapped(django.urls.re_path)
        self.assert_wrapped(django.views.generic.base.View.as_view)

    def assert_not_module_patched(self, django):
        self.assertFalse(hasattr(django, "app"))
        import django.core.handlers.base

        self.assert_not_wrapped(django.core.handlers.base.BaseHandler.load_middleware)
        self.assert_not_wrapped(django.core.handlers.base.BaseHandler.get_response)
        self.assert_not_wrapped(django.template.base.Template.render)
        if django.VERSION >= (2, 0, 0):
            self.assert_not_wrapped(django.urls.path)
            self.assert_not_wrapped(django.urls.re_path)
        import django.views.generic

        self.assert_not_wrapped(django.views.generic.base.View.as_view)

    def assert_not_module_double_patched(self, django):
        self.assert_not_double_wrapped(django.apps.registry.Apps.populate)
        self.assert_not_double_wrapped(django.core.handlers.base.BaseHandler.load_middleware)
        self.assert_not_double_wrapped(django.core.handlers.base.BaseHandler.get_response)
        self.assert_not_wrapped(django.template.base.Template.render)

        if django.VERSION >= (2, 0, 0):
            self.assert_not_double_wrapped(django.urls.path)
            self.assert_not_double_wrapped(django.urls.re_path)
        self.assert_not_double_wrapped(django.views.generic.base.View.as_view)


@pytest.mark.subprocess(ddtrace_run=True, env={"DD_DJANGO_INSTRUMENT_TEMPLATES": "true"})
def test_instrument_templates_patching():
    import django.template.base

    from ddtrace.internal.wrapping import is_wrapped

    assert is_wrapped(django.template.base.Template.render)


@pytest.mark.subprocess(ddtrace_run=True, env={"DD_DJANGO_TRACING_MINIMAL": "false"})
def test_tracing_minimal_patching():
    import django.template.base

    from ddtrace.internal.wrapping import is_wrapped

    assert is_wrapped(django.template.base.Template.render)


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={"DJANGO_SETTINGS_MODULE": "tests.contrib.django.django_app.settings"},
)
def test_setup_does_not_import_root_urlconf():
    """django.setup() must not pull in ROOT_URLCONF.

    Reading resolver.url_patterns imports the URLconf and the entire view import closure behind it. A process that
    never serves a request, such as a Celery or dramatiq worker, would otherwise load all of it for nothing, which
    cost one reporter 154MB of RSS per worker. Management commands are not in scope: Django's own check_url_config
    imports the URLconf whenever system checks run.
    """
    import sys

    import django
    from django.conf import settings

    django.setup()

    assert settings.ROOT_URLCONF not in sys.modules, (
        f"django.setup() imported {settings.ROOT_URLCONF}; endpoint discovery must stay lazy"
    )

    from ddtrace.internal.endpoints import endpoint_collection

    assert not endpoint_collection.endpoints


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={"DJANGO_SETTINGS_MODULE": "tests.contrib.django.django_app.settings"},
)
def test_wsgi_application_collects_endpoints():
    """Building the WSGI application must collect endpoints without serving a request.

    The counterpart of test_setup_does_not_import_root_urlconf: gunicorn and uwsgi import a module that calls
    get_wsgi_application(), which constructs a BaseHandler. That is the point where a process commits to serving
    HTTP, so paying for the URLconf import there is free -- the first request would have paid for it anyway.
    """
    import sys

    from django.conf import settings
    from django.core.wsgi import get_wsgi_application

    get_wsgi_application()

    assert settings.ROOT_URLCONF in sys.modules

    from ddtrace.internal.endpoints import endpoint_collection

    paths = {e.path for e in endpoint_collection.endpoints}
    assert "path/" in paths, paths
    # "test/" mounted under include("...extra_urls") at the "include/" prefix, pinning the sub-application prefix
    # joining from #17695 at startup rather than only per-request.
    assert "include/test/" in paths, paths

    # Counterpart of test_wsgi_application_collects_endpoints_without_middleware_instrumentation: with the flag at
    # its default, moving the middleware wrapping under a runtime check must not have stopped it happening.
    from ddtrace.internal.wrapping import is_wrapped
    from tests.contrib.django.middleware import ClsMiddleware

    assert is_wrapped(ClsMiddleware.__call__)


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DJANGO_SETTINGS_MODULE": "tests.contrib.django.django_app.settings",
        "DD_DJANGO_INSTRUMENT_MIDDLEWARE": "false",
    },
)
def test_wsgi_application_collects_endpoints_without_middleware_instrumentation():
    """Endpoint discovery must not be gated on DD_DJANGO_INSTRUMENT_MIDDLEWARE.

    The walk hangs off BaseHandler.load_middleware, which used to be wrapped only when middleware instrumentation
    was enabled. The middleware assertion keeps the flag itself honest: without it this test would still pass if
    wrapping had accidentally stayed on.
    """
    from django.core.wsgi import get_wsgi_application

    get_wsgi_application()

    from ddtrace.internal.endpoints import endpoint_collection

    assert endpoint_collection.endpoints

    from ddtrace.internal.wrapping import is_wrapped
    from tests.contrib.django.middleware import ClsMiddleware

    assert not is_wrapped(ClsMiddleware.__call__)
