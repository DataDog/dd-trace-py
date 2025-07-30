import pytest


@pytest.fixture
def vllm_request_id():
    """Mock request ID for vLLM tests."""
    return "test-request-123"


@pytest.fixture
def mock_vllm_instance():
    """Mock vLLM instance for testing."""
    class MockLLM:
        def __init__(self, model="test-model"):
            self.model = model
            self.model_config = MockModelConfig(model)
        
        def generate(self, prompts, sampling_params=None):
            return [MockRequestOutput("Generated text")]
        
        def encode(self, prompts, pooling_params=None):
            return [MockEmbeddingOutput([0.1] * 768)]

    class MockModelConfig:
        def __init__(self, model):
            self.model = model
            self.dtype = "float16"
            self.max_model_len = 2048

    class MockRequestOutput:
        def __init__(self, text):
            self.outputs = [MockOutput(text)]

    class MockOutput:
        def __init__(self, text):
            self.text = text

    class MockEmbeddingOutput:
        def __init__(self, embedding):
            self.outputs = MockEmbedding(embedding)

    class MockEmbedding:
        def __init__(self, embedding):
            self.embedding = embedding

    return MockLLM() 