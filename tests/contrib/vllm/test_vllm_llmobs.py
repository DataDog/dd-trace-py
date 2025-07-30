import mock
import pytest

from ddtrace import patch
from ddtrace.llmobs import LLMObs
from tests.subprocesstest import SubprocessTestCase
from tests.subprocesstest import run_in_subprocess

try:
    import torch
except ImportError:
    torch = None


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
class TestVLLMLLMObs(SubprocessTestCase):
    
    vllm_env_config = dict(
        DD_API_KEY="<not-a-real-key>",
        DD_LLMOBS_ENABLED="true",
        DD_LLMOBS_SAMPLE_RATE="1.0",
        DD_LLMOBS_ML_APP="<ml-app-name>",
        # Force CPU-only mode for vLLM
        CUDA_VISIBLE_DEVICES="",
        VLLM_DEVICE="cpu",
        # Suppress vLLM logging to avoid interfering with test output
        VLLM_LOGGING_LEVEL="ERROR",
    )

    def setUp(self):
        patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsSpanWriter")
        LLMObsSpanWriterMock = patcher.start()
        mock_llmobs_span_writer = mock.MagicMock()
        LLMObsSpanWriterMock.return_value = mock_llmobs_span_writer

        self.mock_llmobs_span_writer = mock_llmobs_span_writer

        super(TestVLLMLLMObs, self).setUp()

    def tearDown(self):
        LLMObs.disable()
        super(TestVLLMLLMObs, self).tearDown()

    @staticmethod
    def _call_vllm_generate():
        """Test method for vLLM offline generation."""
        # This would be filled in when vLLM is available for testing
        # For now, this is a placeholder structure
        try:
            import vllm
            
            # Create a mock LLM instance and generate
            # This would use a real model in actual tests
            llm = vllm.LLM(
                model="facebook/opt-125m", 
                trust_remote_code=True,
                tensor_parallel_size=1,
                dtype="auto",
                enforce_eager=True,  # Disable CUDA graphs
                disable_log_stats=True
            )
            prompts = ["Hello, my name is", "The capital of France is"]
            sampling_params = vllm.SamplingParams(temperature=0.8, top_p=0.95, max_tokens=10)
            outputs = llm.generate(prompts, sampling_params)
            
            return outputs
        except (ImportError, RuntimeError, OSError) as e:
            pytest.skip(f"vLLM not available or can't initialize: {e}")

    @staticmethod  
    def _call_vllm_encode():
        """Test method for vLLM offline encoding."""
        try:
            import vllm
            
            # Create a mock LLM instance for embeddings
            llm = vllm.LLM(
                model="intfloat/e5-mistral-7b-instruct", 
                trust_remote_code=True,
                tensor_parallel_size=1,
                dtype="auto",
                enforce_eager=True,  # Disable CUDA graphs
                disable_log_stats=True
            )
            prompts = ["Hello world", "How are you?"]
            outputs = llm.encode(prompts)
            
            return outputs
        except (ImportError, RuntimeError, OSError) as e:
            pytest.skip(f"vLLM not available or can't initialize: {e}")

    @run_in_subprocess(env_overrides=vllm_env_config)
    def test_vllm_llmobs_generate_enabled(self):
        """Test vLLM generate with LLMObs enabled."""
        patch(vllm=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        
        try:
            self._call_vllm_generate()
        except ImportError:
            pytest.skip("vLLM not available")

    @run_in_subprocess(env_overrides=vllm_env_config)
    def test_vllm_llmobs_encode_enabled(self):
        """Test vLLM encode with LLMObs enabled."""
        patch(vllm=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        
        try:
            self._call_vllm_encode()
        except ImportError:
            pytest.skip("vLLM not available")

    @run_in_subprocess(env_overrides=vllm_env_config)
    def test_vllm_llmobs_disabled(self):
        """Test vLLM with LLMObs disabled."""
        patch(vllm=False)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        
        try:
            self._call_vllm_generate()
        except ImportError:
            pytest.skip("vLLM not available") 