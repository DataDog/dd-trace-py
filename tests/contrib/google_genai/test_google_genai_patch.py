from ddtrace.contrib.internal.google_genai.patch import get_version
from ddtrace.contrib.internal.google_genai.patch import patch
from ddtrace.contrib.internal.google_genai.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestGoogleGenAIPatch(PatchTestCase.Base):
    __integration_name__ = "google_genai"
    __module_name__ = "google.genai"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, google_genai):
        self.assert_wrapped(google_genai.models.Models.generate_content)
        self.assert_wrapped(google_genai.models.Models.generate_content_stream)
        self.assert_wrapped(google_genai.models.AsyncModels.generate_content)
        self.assert_wrapped(google_genai.models.AsyncModels.generate_content_stream)

    def assert_not_module_patched(self, google_genai):
        self.assert_not_wrapped(google_genai.models.Models.generate_content)
        self.assert_not_wrapped(google_genai.models.Models.generate_content_stream)
        self.assert_not_wrapped(google_genai.models.AsyncModels.generate_content)
        self.assert_not_wrapped(google_genai.models.AsyncModels.generate_content_stream)

    def assert_not_module_double_patched(self, google_genai):
        self.assert_not_double_wrapped(google_genai.models.Models.generate_content)
        self.assert_not_double_wrapped(google_genai.models.Models.generate_content_stream)
        self.assert_not_double_wrapped(google_genai.models.AsyncModels.generate_content)
        self.assert_not_double_wrapped(google_genai.models.AsyncModels.generate_content_stream)
