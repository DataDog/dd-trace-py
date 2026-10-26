import websockets

from ddtrace.contrib.internal.elevenlabs.patch import get_version
from ddtrace.contrib.internal.elevenlabs.patch import patch
from ddtrace.contrib.internal.elevenlabs.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestElevenLabsPatch(PatchTestCase.Base):
    __integration_name__ = "elevenlabs"
    __module_name__ = "elevenlabs"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def _methods(self, elevenlabs):
        conversation = elevenlabs.conversational_ai.conversation
        return [
            conversation.Conversation.start_session,
            conversation.Conversation._run,
            conversation.Conversation.end_session,
            conversation.AsyncConversation.start_session,
            conversation.AsyncConversation._run,
            conversation.AsyncConversation.end_session,
            conversation.ClientTools.handle,
            conversation.connect,
        ]

    def assert_module_patched(self, elevenlabs):
        for method in self._methods(elevenlabs):
            self.assert_wrapped(method)
        self.assertIs(elevenlabs.conversational_ai.conversation.websockets.__wrapped__, websockets)
        self.assert_not_wrapped(websockets.connect)

    def assert_not_module_patched(self, elevenlabs):
        for method in self._methods(elevenlabs):
            self.assert_not_wrapped(method)
        self.assertIs(elevenlabs.conversational_ai.conversation.websockets, websockets)
        self.assert_not_wrapped(websockets.connect)

    def assert_not_module_double_patched(self, elevenlabs):
        for method in self._methods(elevenlabs):
            self.assert_not_double_wrapped(method)
        self.assertIs(elevenlabs.conversational_ai.conversation.websockets.__wrapped__, websockets)
