from ddtrace.contrib.internal.mistralai.patch import get_version
from ddtrace.contrib.internal.mistralai.patch import patch
from ddtrace.contrib.internal.mistralai.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestMistralAIPatch(PatchTestCase.Base):
    __integration_name__ = "mistralai"
    __module_name__ = "mistralai.client"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, mistralai):
        self.assert_wrapped(mistralai.chat.Chat.complete)
        self.assert_wrapped(mistralai.chat.Chat.complete_async)
        self.assert_wrapped(mistralai.embeddings.Embeddings.create)
        self.assert_wrapped(mistralai.embeddings.Embeddings.create_async)

    def assert_not_module_patched(self, mistralai):
        self.assert_not_wrapped(mistralai.chat.Chat.complete)
        self.assert_not_wrapped(mistralai.chat.Chat.complete_async)
        self.assert_not_wrapped(mistralai.embeddings.Embeddings.create)
        self.assert_not_wrapped(mistralai.embeddings.Embeddings.create_async)

    def assert_not_module_double_patched(self, mistralai):
        self.assert_not_double_wrapped(mistralai.chat.Chat.complete)
        self.assert_not_double_wrapped(mistralai.chat.Chat.complete_async)
        self.assert_not_double_wrapped(mistralai.embeddings.Embeddings.create)
        self.assert_not_double_wrapped(mistralai.embeddings.Embeddings.create_async)
