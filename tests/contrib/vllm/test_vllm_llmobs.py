import mock
import pytest

from ddtrace import patch
from ddtrace.llmobs import LLMObs


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [
        dict(
            _llmobs_enabled=True,
            _llmobs_sample_rate=1.0,
            _llmobs_ml_app="<ml-app-name>",
        )
    ],
    indirect=True,
)
class TestVLLMLLMObs:
    
    def test_vllm_llmobs_generate_enabled(self, vllm, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Test vLLM generate with LLMObs enabled."""
        with mock.patch.object(vllm.LLM, "__init__", return_value=None):
            with mock.patch.object(vllm.LLM, "generate") as mock_generate:
                # Mock the generate response
                mock_output = mock.MagicMock()
                mock_output.outputs = [mock.MagicMock()]
                mock_output.outputs[0].text = "Hello, I'm an AI assistant"
                mock_generate.return_value = [mock_output]
                
                # Initialize LLM and generate
                llm = vllm.LLM(model="gpt2-medium")
                sampling_params = vllm.SamplingParams(temperature=0.8, max_tokens=10)
                outputs = llm.generate(["Hello, my name is"], sampling_params)
                
                # Verify mock was called
                mock_generate.assert_called_once()
                
                # Verify span was created and LLMObs event was queued
                span = mock_tracer.pop_traces()[0][0]
                assert mock_llmobs_writer.enqueue.call_count == 1

    def test_vllm_llmobs_encode_enabled(self, vllm, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Test vLLM encode with LLMObs enabled."""
        with mock.patch.object(vllm.LLM, "__init__", return_value=None):
            with mock.patch.object(vllm.LLM, "encode") as mock_encode:
                # Mock the encode response
                mock_output = mock.MagicMock()
                mock_output.outputs = mock.MagicMock()
                mock_output.outputs.embedding = [0.1] * 768
                mock_encode.return_value = [mock_output]
                
                # Initialize LLM and encode
                llm = vllm.LLM(model="gpt2-medium")
                outputs = llm.encode(["Hello world"])
                
                # Verify mock was called
                mock_encode.assert_called_once()
                
                # Verify span was created and LLMObs event was queued  
                span = mock_tracer.pop_traces()[0][0]
                assert mock_llmobs_writer.enqueue.call_count == 1 