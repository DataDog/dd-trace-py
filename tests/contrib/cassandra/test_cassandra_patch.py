from ddtrace.contrib.cassandra import patch
# from ddtrace.contrib.cassandra import unpatch
from tests.contrib.patch import PatchTestCase


class TestCassandraPatch(PatchTestCase.Base):
    __integration_name__ = "cassandra"
    __module_name__ = "cassandra.cluster"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, cassandra_cluster):
        pass

    def assert_not_module_patched(self, cassandra_cluster):
        pass

    def assert_not_module_double_patched(self, cassandra_cluster):
        pass

