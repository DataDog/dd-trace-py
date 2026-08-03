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

    Reading resolver.url_patterns imports the URLconf and the entire view import
    closure behind it. Processes that never serve a request (Celery/dramatiq
    workers, management commands, cron jobs) would otherwise load all of it for
    nothing, which cost one reporter 150MB of RSS per worker.
    """
    import sys

    import django
    from django.conf import settings

    django.setup()

    assert settings.ROOT_URLCONF not in sys.modules, (
        f"django.setup() imported {settings.ROOT_URLCONF}; endpoint discovery must stay lazy"
    )
