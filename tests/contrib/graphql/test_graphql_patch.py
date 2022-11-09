from ddtrace.contrib.graphql import patch
# from ddtrace.contrib.graphql import unpatch
from tests.contrib.patch import PatchTestCase


class TestGraphqlPatch(PatchTestCase.Base):
    __integration_name__ = "graphql"
    __module_name__ = "graphql"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, graphql):
        pass

    def assert_not_module_patched(self, graphql):
        pass

    def assert_not_module_double_patched(self, graphql):
        pass

