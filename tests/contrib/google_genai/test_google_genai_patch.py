import importlib

from ddtrace.contrib.internal.google_genai.patch import get_version
from ddtrace.contrib.internal.google_genai.patch import patch
from ddtrace.contrib.internal.google_genai.patch import unpatch
from tests.contrib.patch import PatchTestCase


def _decision_methods():
    try:
        module = importlib.import_module("google.genai._gaos.google_genai")
    except ImportError:
        return []
    if not hasattr(module, "GeminiNextGenDecisions"):
        return []
    return [module.GeminiNextGenDecisions.create, module.AsyncGeminiNextGenDecisions.create]


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
        self.assert_wrapped(google_genai.models.Models.embed_content)
        self.assert_wrapped(google_genai.models.AsyncModels.embed_content)
        for method in _decision_methods():
            self.assert_wrapped(method)

    def assert_not_module_patched(self, google_genai):
        self.assert_not_wrapped(google_genai.models.Models.generate_content)
        self.assert_not_wrapped(google_genai.models.Models.generate_content_stream)
        self.assert_not_wrapped(google_genai.models.AsyncModels.generate_content)
        self.assert_not_wrapped(google_genai.models.AsyncModels.generate_content_stream)
        self.assert_not_wrapped(google_genai.models.Models.embed_content)
        self.assert_not_wrapped(google_genai.models.AsyncModels.embed_content)
        for method in _decision_methods():
            self.assert_not_wrapped(method)

    def assert_not_module_double_patched(self, google_genai):
        self.assert_not_double_wrapped(google_genai.models.Models.generate_content)
        self.assert_not_double_wrapped(google_genai.models.Models.generate_content_stream)
        self.assert_not_double_wrapped(google_genai.models.AsyncModels.generate_content)
        self.assert_not_double_wrapped(google_genai.models.AsyncModels.generate_content_stream)
        self.assert_not_double_wrapped(google_genai.models.Models.embed_content)
        self.assert_not_double_wrapped(google_genai.models.AsyncModels.embed_content)
        for method in _decision_methods():
            self.assert_not_double_wrapped(method)
