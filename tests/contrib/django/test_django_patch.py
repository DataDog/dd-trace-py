from ddtrace.contrib.django import patch
from tests.contrib.patch import PatchTestCase


class TestDjangoPatch(PatchTestCase.Base):
    __integration_name__ = 'django'
    __module_name__ = 'django'
    __patch_func__ = patch
    __unpatch_func__ = None

    def assert_module_patched(self, django):
        self.assert_wrapped(django.apps.registry.Apps.populate)
        self.assert_wrapped(django.core.handlers.base.BaseHandler.load_middleware)
        self.assert_wrapped(django.core.handlers.base.BaseHandler.get_response)
        self.assert_wrapped(django.template.base.Template.render)

    def assert_not_module_patched(self, django):
        self.assert_not_wrapped(django.apps.registry.Apps.populate)
        self.assert_not_wrapped(django.core.handlers.base.BaseHandler.load_middleware)
        self.assert_not_wrapped(django.core.handlers.base.BaseHandler.get_response)
        self.assert_not_wrapped(django.template.base.Template.render)

    def assert_not_module_double_patched(self, django):
        self.assert_not_double_wrapped(django.apps.registry.Apps.populate)
        self.assert_not_double_wrapped(django.core.handlers.base.BaseHandler.load_middleware)
        self.assert_not_double_wrapped(django.core.handlers.base.BaseHandler.get_response)
        self.assert_not_double_wrapped(django.template.base.Template.render)
