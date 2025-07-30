from ddtrace.contrib.internal.vllm.patch import get_version
from ddtrace.contrib.internal.vllm.patch import patch
from ddtrace.contrib.internal.vllm.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestVLLMPatch(PatchTestCase.Base):
    __integration_name__ = "vllm"
    __module_name__ = "vllm"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, vllm):
        # Test that LLM class methods are patched
        try:
            self.assert_wrapped(vllm.LLM.generate)
            self.assert_wrapped(vllm.LLM.encode)
        except (AttributeError, ImportError):
            pass  # Methods might not exist in all versions

        # Test that AsyncLLMEngine methods are patched
        try:
            self.assert_wrapped(vllm.AsyncLLMEngine.generate)
            self.assert_wrapped(vllm.AsyncLLMEngine.encode)
        except (AttributeError, ImportError):
            pass  # Methods might not exist in all versions

        # Test the critical llm_request operation (LLMEngine.step) if available
        try:
            from vllm.engine.llm_engine import LLMEngine
            self.assert_wrapped(LLMEngine.step)
        except (AttributeError, ImportError):
            pass  # This captures the full request lifecycle like vLLM's llm_request span

    def assert_not_module_patched(self, vllm):
        # Test that LLM class methods are not patched
        try:
            self.assert_not_wrapped(vllm.LLM.generate)
            self.assert_not_wrapped(vllm.LLM.encode)
        except (AttributeError, ImportError):
            pass

        # Test that AsyncLLMEngine methods are not patched
        try:
            self.assert_not_wrapped(vllm.AsyncLLMEngine.generate)
            self.assert_not_wrapped(vllm.AsyncLLMEngine.encode)
        except (AttributeError, ImportError):
            pass

        # Test the critical llm_request operation (LLMEngine.step) if available
        try:
            from vllm.engine.llm_engine import LLMEngine
            self.assert_not_wrapped(LLMEngine.step)
        except (AttributeError, ImportError):
            pass

    def assert_not_module_double_patched(self, vllm):
        # Test that LLM class methods are not double patched
        try:
            self.assert_not_double_wrapped(vllm.LLM.generate)
            self.assert_not_double_wrapped(vllm.LLM.encode)
        except (AttributeError, ImportError):
            pass

        # Test that AsyncLLMEngine methods are not double patched
        try:
            self.assert_not_double_wrapped(vllm.AsyncLLMEngine.generate)
            self.assert_not_double_wrapped(vllm.AsyncLLMEngine.encode)
        except (AttributeError, ImportError):
            pass

        # Test the critical llm_request operation (LLMEngine.step) if available
        try:
            from vllm.engine.llm_engine import LLMEngine
            self.assert_not_double_wrapped(LLMEngine.step)
        except (AttributeError, ImportError):
            pass


def test_mock_vllm_instance_generate(mock_vllm_instance):
    """Example test showing how to use the mock_vllm_instance fixture."""
    # Test LLM generation
    result = mock_vllm_instance.generate(["Hello, world!"])
    
    assert len(result) == 1
    assert len(result[0].outputs) == 1
    assert result[0].outputs[0].text == "Generated text"
    
    # Test model config access
    assert mock_vllm_instance.model == "test-model"
    assert mock_vllm_instance.model_config.model == "test-model"
    assert mock_vllm_instance.model_config.dtype == "float16"
    assert mock_vllm_instance.model_config.max_model_len == 2048


def test_mock_vllm_instance_encode(mock_vllm_instance):
    """Example test showing how to use the mock_vllm_instance fixture for embeddings."""
    # Test LLM encoding/embeddings
    result = mock_vllm_instance.encode(["Hello, world!"])
    
    assert len(result) == 1
    assert len(result[0].outputs.embedding) == 768
    assert result[0].outputs.embedding[0] == 0.1 