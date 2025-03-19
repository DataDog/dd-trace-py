from ddtrace.contrib.internal.crewai.patch import get_version
from ddtrace.contrib.internal.crewai.patch import patch
from ddtrace.contrib.internal.crewai.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestAgentsPatch(PatchTestCase.Base):
    __integration_name__ = "agents"
    __module_name__ = "agents"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, agents):
        pass

    def assert_not_module_patched(self, agents):
        pass

    def assert_not_module_double_patched(self, agents):
        pass
