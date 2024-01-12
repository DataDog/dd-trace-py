import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
import uuid

from ddtrace import Span
from ddtrace import config

from .base import BaseLLMIntegration


class BedrockIntegration(BaseLLMIntegration):
    _integration_name = "bedrock"

    @classmethod
    def _llmobs_tags(cls, span: Span) -> List[str]:
        tags = [
            "version:%s" % (config.version or ""),
            "env:%s" % (config.env or ""),
            "service:%s" % (span.service or ""),
            "src:integration",
            "ml_obs.request.model:%s" % (span.get_tag("bedrock.request.model") or ""),
            "ml_obs.request.model_provider:%s" % (span.get_tag("bedrock.request.model_provider") or ""),
            "ml_obs.request.error:%d" % span.error,
        ]
        err_type = span.get_tag("error.type")
        if err_type:
            tags.append("error_type:%s" % err_type)
        return tags

    def generate_llm_record(
        self, span: Span, formatted_response: Dict[str, Any] = None, prompt: Optional[str] = None, err: bool = False
    ) -> None:
        """Generate payloads for the LLM Obs API from a completion."""
        if not self.llmobs_enabled:
            return
        now = time.time()
        if err:
            attrs_dict = {
                "type": "completion",
                "id": str(uuid.uuid4()),
                "timestamp": int(span.start * 1000),
                "model": span.get_tag("bedrock.request.model"),
                "model_provider": span.get_tag("bedrock.request.model_provider"),
                "input": {
                    "prompts": [prompt],
                    "temperature": float(span.get_tag("bedrock.request.temperature")),
                    "max_tokens": int(span.get_tag("bedrock.request.max_tokens")),
                },
                "output": {
                    "completions": [{"content": ""}],
                    "durations": [now - span.start],
                    "errors": [span.get_tag("error.message")],
                },
            }
            self.llm_record(span, attrs_dict)
            return
        for i in range(len(formatted_response["text"])):
            prompt_tokens = int(span.get_tag("bedrock.usage.prompt_tokens"))
            completion_tokens = int(span.get_tag("bedrock.usage.completion_tokens"))
            attrs_dict = {
                "type": "completion",
                "id": span.get_tag("bedrock.response.id"),
                "timestamp": int(span.start * 1000),
                "model": span.get_tag("bedrock.request.model"),
                "model_provider": span.get_tag("bedrock.request.model_provider"),
                "input": {
                    "prompts": [prompt],
                    "temperature": float(span.get_tag("bedrock.request.temperature")),
                    "max_tokens": int(span.get_tag("bedrock.request.max_tokens")),
                    "prompt_tokens": [prompt_tokens],
                },
                "output": {
                    "completions": [{"content": formatted_response["text"][i]}],
                    "durations": [now - span.start],
                    "completion_tokens": [completion_tokens],
                    "total_tokens": [prompt_tokens + completion_tokens],
                },
            }
            self.llm_record(span, attrs_dict)
