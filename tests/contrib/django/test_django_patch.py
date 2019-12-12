from ddtrace.contrib.django import patch, unpatch
from tests.contrib.patch import PatchTestCase


"""
class TestDjangoPatch(PatchTestCase.Base):
    __integration_name__ = 'django'
    __module_name__ = 'django'
    __patch_func__ = patch
    __unpatch_func__ = unpatch

    def assert_module_patched(self, django):
        self.assert_wrapped(django.core.handlers.base.BaseHandler.load_middleware)

    def assert_not_module_patched(self, django):
        pass

    def assert_not_module_double_patched(self, django):
        pass
"""
