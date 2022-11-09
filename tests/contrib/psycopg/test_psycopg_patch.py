from ddtrace.contrib.psycopg import patch
# from ddtrace.contrib.psycopg import unpatch
from tests.contrib.patch import PatchTestCase


class TestPsycopgPatch(PatchTestCase.Base):
    __integration_name__ = "psycopg"
    __module_name__ = "psycopg2"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, psycopg2):
        pass

    def assert_not_module_patched(self, psycopg2):
        pass

    def assert_not_module_double_patched(self, psycopg2):
        pass

