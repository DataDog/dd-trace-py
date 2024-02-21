import json
from typing import Any
from typing import Dict
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.llmobs._integrations import BaseLLMIntegration


class BedrockIntegration(BaseLLMIntegration):
    _integration_name = "bedrock"

    def llmobs_set_tags(
        self,
        span: Span,
        formatted_response: Optional[Dict[str, Any]] = None,
        prompt: Optional[str] = None,
        err: bool = False,
    ) -> None:
        if not self.llmobs_enabled:
            return
        metrics = self._set_llmobs_metrics(span, formatted_response)
        meta = {
            "model_name": span.get_tag("bedrock.request.model"),
            "model_provider": span.get_tag("bedrock.request.model_provider"),
            "span.kind": "llm",
            "input": {
                "messages": [{"content": prompt}],
                "parameters": {
                    "temperature": float(span.get_tag("bedrock.request.temperature") or 0.0),
                    "max_tokens": int(span.get_tag("bedrock.request.max_tokens") or 0),
                },
            },
        }
        if err or formatted_response is None:
            meta["output"] = {"messages": [{"content": ""}]}
        else:
            meta["output"] = {"messages": [{"content": completion} for completion in formatted_response["text"]]}
        # Since span tags have to be strings, we have to json dump the data here and load on the trace processor.
        span.set_tag_str("ml_obs.meta", json.dumps(meta))
        span.set_tag_str("ml_obs.metrics", json.dumps(metrics))

    @staticmethod
    def _set_llmobs_metrics(span: Span, formatted_response: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        metrics = {}
        if formatted_response and formatted_response.get("text"):
            prompt_tokens = int(span.get_tag("bedrock.usage.prompt_tokens") or 0)
            completion_tokens = int(span.get_tag("bedrock.usage.completion_tokens") or 0)
            metrics["prompt_tokens"] = prompt_tokens
            metrics["completion_tokens"] = completion_tokens
            metrics["total_tokens"] = prompt_tokens + completion_tokens
        return metrics
