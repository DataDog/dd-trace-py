from ddtrace.contrib.graphql import patch
from tests.contrib.patch import PatchTestCase


class TestGraphqlPatch(PatchTestCase.Base):
    __integration_name__ = "graphql"
    __module_name__ = "graphql"
    __patch_func__ = patch
    __unpatch_func__ = None

    def assert_module_patched(self, graphql):
        self.assert_wrapped(graphql.graphql)
        self.assert_wrapped(graphql.graphql_sync)

    def assert_not_module_patched(self, graphql):
        self.assert_not_wrapped(graphql.graphql)
        self.assert_not_wrapped(graphql.graphql_sync)

    def assert_not_module_double_patched(self, graphql):
        self.assert_not_double_wrapped(graphql.graphql)
        self.assert_not_double_wrapped(graphql.graphql_sync)
