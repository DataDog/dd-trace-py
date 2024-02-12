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
            "source:integration",
            "model_name:%s" % (span.get_tag("bedrock.request.model") or ""),
            "model_provider:%s" % (span.get_tag("bedrock.request.model_provider") or ""),
            "error:%d" % span.error,
        ]
        err_type = span.get_tag("error.type")
        if err_type:
            tags.append("error_type:%s" % err_type)
        return tags

    def generate_llm_record(
        self,
        span: Span,
        formatted_response: Optional[Dict[str, Any]] = None,
        prompt: Optional[str] = None,
        err: bool = False,
    ) -> None:
        """Generate payloads for the LLM Obs API from a completion."""
        if not self.llmobs_enabled:
            return
        if err or formatted_response is None:
            record = _llmobs_record(span, prompt)
            record["id"] = str(uuid.uuid4())
            record["output"]["completions"] = [{"content": ""}]
            record["output"]["errors"] = [span.get_tag("error.message")]
            self.llm_record(span, record)
            return
        for i in range(len(formatted_response["text"])):
            prompt_tokens = int(span.get_tag("bedrock.usage.prompt_tokens") or 0)
            completion_tokens = int(span.get_tag("bedrock.usage.completion_tokens") or 0)
            record = _llmobs_record(span, prompt)
            record["id"] = span.get_tag("bedrock.response.id")
            record["input"]["prompt_tokens"] = [prompt_tokens]
            record["output"]["completions"] = [{"content": formatted_response["text"][i]}]
            record["output"]["completion_tokens"] = [completion_tokens]
            record["output"]["total_tokens"] = [prompt_tokens + completion_tokens]
            self.llm_record(span, record)


def _llmobs_record(span: Span, prompt: Optional[str]) -> Dict[str, Any]:
    """LLMObs bedrock record template."""
    now = time.time()
    record = {
        "type": "completion",
        "id": str(uuid.uuid4()),
        "timestamp": int(span.start * 1000),
        "model": span.get_tag("bedrock.request.model"),
        "model_provider": span.get_tag("bedrock.request.model_provider"),
        "input": {
            "prompts": [prompt],
            "temperature": float(span.get_tag("bedrock.request.temperature") or 0.0),
            "max_tokens": int(span.get_tag("bedrock.request.max_tokens") or 0),
        },
        "output": {
            "durations": [now - span.start],
        },
    }
    return record
