from ddtrace.contrib.snowflake import get_version
from ddtrace.contrib.snowflake import patch
from tests.contrib.patch import PatchTestCase


class TestSnowflakePatch(PatchTestCase.Base):
    __integration_name__ = "snowflake"
    __module_name__ = "snowflake.connector"
    __patch_func__ = patch
    __unpatch_func__ = None
    __get_version__ = get_version

    def assert_module_patched(self, snowflake):
        self.assert_wrapped(snowflake.connect)

    def assert_not_module_patched(self, snowflake):
        self.assert_not_wrapped(snowflake.connect)

    def assert_not_module_double_patched(self, snowflake):
        self.assert_not_double_wrapped(snowflake.connect)
