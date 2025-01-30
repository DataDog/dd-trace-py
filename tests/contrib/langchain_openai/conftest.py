import os
import pytest

from ddtrace.contrib.internal.langchain_openai.patch import patch
from ddtrace.contrib.internal.langchain_openai.patch import unpatch
from tests.utils import override_config
from tests.utils import override_env
from tests.utils import override_global_config

@pytest.fixture
def langchain_openai():
    with override_global_config(dict(_dd_api_key="<not-a-real-key>")):
        with override_config("langchain", {}):
            with override_env(
                dict(
                    OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", "<not-a-real-key>"),
                    COHERE_API_KEY=os.getenv("COHERE_API_KEY", "<not-a-real-key>"),
                    ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", "<not-a-real-key>"),
                    HUGGINGFACEHUB_API_TOKEN=os.getenv("HUGGINGFACEHUB_API_TOKEN", "<not-a-real-key>"),
                    AI21_API_KEY=os.getenv("AI21_API_KEY", "<not-a-real-key>"),
                )
            ):
                patch()
                import langchain_openai

                yield langchain_openai
                unpatch()  