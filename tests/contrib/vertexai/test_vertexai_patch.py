import pytest

from ddtrace.contrib.internal.vertexai.patch import get_version
from ddtrace.contrib.internal.vertexai.patch import patch
from ddtrace.contrib.internal.vertexai.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestVertexAIPatch(PatchTestCase.Base):
    __integration_name__ = "vertexai"
    __module_name__ = "vertexai"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    @pytest.mark.skip(reason="vertexai imports print warnings to stdout, corrupting test output on CI")
    def test_ddtrace_run_patch_on_import(self):
        pass

    @pytest.mark.skip(reason="vertexai imports print warnings to stdout, corrupting test output on CI")
    def test_supported_versions_function_allows_valid_imports(self):
        pass

    def assert_module_patched(self, vertexai):
        self.assert_wrapped(vertexai.generative_models.GenerativeModel.generate_content)
        self.assert_wrapped(vertexai.generative_models.GenerativeModel.generate_content_async)
        self.assert_wrapped(vertexai.generative_models.ChatSession.send_message)
        self.assert_wrapped(vertexai.generative_models.ChatSession.send_message_async)

    def assert_not_module_patched(self, vertexai):
        self.assert_not_wrapped(vertexai.generative_models.GenerativeModel.generate_content)
        self.assert_not_wrapped(vertexai.generative_models.GenerativeModel.generate_content_async)
        self.assert_not_wrapped(vertexai.generative_models.ChatSession.send_message)
        self.assert_not_wrapped(vertexai.generative_models.ChatSession.send_message_async)

    def assert_not_module_double_patched(self, vertexai):
        self.assert_not_double_wrapped(vertexai.generative_models.GenerativeModel.generate_content)
        self.assert_not_double_wrapped(vertexai.generative_models.GenerativeModel.generate_content_async)
        self.assert_not_double_wrapped(vertexai.generative_models.ChatSession.send_message)
        self.assert_not_double_wrapped(vertexai.generative_models.ChatSession.send_message_async)
