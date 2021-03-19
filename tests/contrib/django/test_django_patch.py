import os

import pytest

from ddtrace.contrib.django import patch
from tests.contrib.patch import PatchTestCase


pytestmark = pytest.mark.skipif("TEST_DATADOG_DJANGO_MIGRATION" in os.environ, reason="test only without migration")


class TestDjangoPatch(PatchTestCase.Base):
    __integration_name__ = "django"
    __module_name__ = "django"
    __patch_func__ = patch
    __unpatch_func__ = None

    def assert_module_patched(self, django):
        self.assert_wrapped(django.apps.registry.Apps.populate)
        self.assert_wrapped(django.core.handlers.base.BaseHandler.load_middleware)
        self.assert_wrapped(django.core.handlers.base.BaseHandler.get_response)
        self.assert_wrapped(django.template.base.Template.render)
        if django.VERSION >= (2, 0, 0):
            self.assert_wrapped(django.urls.path)
            self.assert_wrapped(django.urls.re_path)
        self.assert_wrapped(django.views.generic.base.View.as_view)
        self.assert_wrapped(django.db.connections.__setitem__)

    def assert_not_module_patched(self, django):
        self.assert_not_wrapped(django.apps.registry.Apps.populate)
        self.assert_not_wrapped(django.core.handlers.base.BaseHandler.load_middleware)
        self.assert_not_wrapped(django.core.handlers.base.BaseHandler.get_response)
        self.assert_not_wrapped(django.template.base.Template.render)
        if django.VERSION >= (2, 0, 0):
            self.assert_not_wrapped(django.urls.path)
            self.assert_not_wrapped(django.urls.re_path)
        self.assert_not_wrapped(django.views.generic.base.View.as_view)
        self.assert_not_wrapped(django.db.connections.__setitem__)

    def assert_not_module_double_patched(self, django):
        self.assert_not_double_wrapped(django.apps.registry.Apps.populate)
        self.assert_not_double_wrapped(django.core.handlers.base.BaseHandler.load_middleware)
        self.assert_not_double_wrapped(django.core.handlers.base.BaseHandler.get_response)
        self.assert_not_double_wrapped(django.template.base.Template.render)
        if django.VERSION >= (2, 0, 0):
            self.assert_not_double_wrapped(django.urls.path)
            self.assert_not_double_wrapped(django.urls.re_path)
        self.assert_not_double_wrapped(django.views.generic.base.View.as_view)
        self.assert_not_double_wrapped(django.db.connections.__setitem__)
