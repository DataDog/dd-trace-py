from ddtrace.contrib.internal.claude_agent_sdk.patch import get_version
from ddtrace.contrib.internal.claude_agent_sdk.patch import patch


try:
    from ddtrace.contrib.internal.claude_agent_sdk.patch import unpatch
except ImportError:
    unpatch = None

from tests.contrib.patch import PatchTestCase


class TestClaudeAgentSdkPatch(PatchTestCase.Base):
    __integration_name__ = "claude_agent_sdk"
    __module_name__ = "claude_agent_sdk"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, claude_agent_sdk):
        # Verify the standalone query function is wrapped
        self.assert_wrapped(claude_agent_sdk.query)
        # Verify ClaudeSDKClient.query method is wrapped
        self.assert_wrapped(claude_agent_sdk.ClaudeSDKClient.query)

    def assert_not_module_patched(self, claude_agent_sdk):
        # Verify the standalone query function is NOT wrapped
        self.assert_not_wrapped(claude_agent_sdk.query)
        # Verify ClaudeSDKClient.query method is NOT wrapped
        self.assert_not_wrapped(claude_agent_sdk.ClaudeSDKClient.query)

    def assert_not_module_double_patched(self, claude_agent_sdk):
        # Verify the standalone query function is NOT double-wrapped
        self.assert_not_double_wrapped(claude_agent_sdk.query)
        # Verify ClaudeSDKClient.query method is NOT double-wrapped
        self.assert_not_double_wrapped(claude_agent_sdk.ClaudeSDKClient.query)
