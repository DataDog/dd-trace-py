from ddtrace.contrib.internal.google_generativeai.patch import get_version
from ddtrace.contrib.internal.google_generativeai.patch import patch
from ddtrace.contrib.internal.google_generativeai.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestGoogleGenerativeAIPatch(PatchTestCase.Base):
    __integration_name__ = "google_generativeai"
    __module_name__ = "google.generativeai"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, genai):
        self.assert_wrapped(genai.GenerativeModel.generate_content)
        self.assert_wrapped(genai.GenerativeModel.generate_content_async)

    def assert_not_module_patched(self, genai):
        self.assert_not_wrapped(genai.GenerativeModel.generate_content)
        self.assert_not_wrapped(genai.GenerativeModel.generate_content_async)

    def assert_not_module_double_patched(self, genai):
        self.assert_not_double_wrapped(genai.GenerativeModel.generate_content)
        self.assert_not_double_wrapped(genai.GenerativeModel.generate_content_async)
