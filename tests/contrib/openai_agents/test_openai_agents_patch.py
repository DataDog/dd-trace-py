import pytest

from ddtrace.contrib.internal.openai_agents.patch import get_version
from ddtrace.contrib.internal.openai_agents.patch import patch
from ddtrace.contrib.internal.openai_agents.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestAgentsPatch(PatchTestCase.Base):
    __integration_name__ = "openai_agents"
    __module_name__ = "agents"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    @pytest.mark.skip(reason="Not applicable for openai_agents")
    def test_ddtrace_run_patch_on_import(self):
        # This overrides the parent method and skips it
        pass

    def assert_module_patched(self, agents):
        pass

    def assert_not_module_patched(self, agents):
        pass

    def assert_not_module_double_patched(self, agents):
        pass
