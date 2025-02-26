from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations import BaseLLMIntegration
from ddtrace.llmobs._integrations.utils import get_llmobs_metrics_tags
from ddtrace.trace import Span


log = get_logger(__name__)


class BedrockIntegration(BaseLLMIntegration):
    _integration_name = "bedrock"

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Extract prompt/response tags from a completion and set them as temporary "_ml_obs.*" tags."""
        LLMObs._instance._activate_llmobs_span(span)
        parameters = {}
        if span.get_tag("bedrock.request.temperature"):
            parameters["temperature"] = float(span.get_tag("bedrock.request.temperature") or 0.0)
        if span.get_tag("bedrock.request.max_tokens"):
            parameters["max_tokens"] = int(span.get_tag("bedrock.request.max_tokens") or 0)
        
        # Handle different input formats from different API calls
        prompt = kwargs.get("prompt", "")
        
        # Special handling for Converse API which passes the messages directly
        if isinstance(prompt, list) and all(isinstance(msg, dict) and "role" in msg for msg in prompt):
            # This is already in the messages format
            input_messages = self._extract_input_message(prompt)
        # Special case for raw messages array from Converse API
        elif "messages" in kwargs and isinstance(kwargs["messages"], list):
            # If we got raw messages directly from the Converse API
            input_messages = self._extract_input_message(kwargs["messages"])
        else:
            # Default handling
            input_messages = self._extract_input_message(prompt)
            
        # Handle output
        output_messages = [{"content": ""}]
        if not span.error and response is not None:
            output_messages = self._extract_output_message(response)
            
        span._set_ctx_items(
            {
                SPAN_KIND: "llm",
                MODEL_NAME: span.get_tag("bedrock.request.model") or "",
                MODEL_PROVIDER: span.get_tag("bedrock.request.model_provider") or "",
                INPUT_MESSAGES: input_messages,
                METADATA: parameters,
                METRICS: get_llmobs_metrics_tags("bedrock", span),
                OUTPUT_MESSAGES: output_messages,
            }
        )

    @staticmethod
    def _extract_input_message(prompt):
        """Extract input messages from the stored prompt.
        Anthropic allows for messages and multiple texts in a message, which requires some special casing.
        Bedrock's Converse API uses a specific message format that needs special handling.
        """
        if isinstance(prompt, str):
            return [{"content": prompt}]
        if not isinstance(prompt, list):
            log.warning("Bedrock input is not a list of messages or a string.")
            return [{"content": ""}]
        
        input_messages = []
        for p in prompt:
            if not isinstance(p, dict):
                continue
                
            role = str(p.get("role", ""))
            content = p.get("content", "")
            
            # Handle different content formats
            if isinstance(content, list):
                # Structured content array with multiple parts
                if content and isinstance(content[0], dict):
                    combined_text = ""
                    for entry in content:
                        # Text content
                        if entry.get("type") == "text":
                            combined_text += entry.get("text", "") + " "
                        # Direct text field
                        elif "text" in entry:
                            combined_text += entry.get("text", "") + " "
                        # Image content
                        elif entry.get("type") == "image":
                            # Store a placeholder for potentially enormous binary image data
                            input_messages.append({"content": "([IMAGE DETECTED])", "role": role})
                    
                    if combined_text:
                        input_messages.append({"content": combined_text.strip(), "role": role})
                # Array of strings
                elif content and isinstance(content[0], str):
                    combined_text = " ".join(content)
                    input_messages.append({"content": combined_text, "role": role})
            # String content
            elif isinstance(content, str):
                input_messages.append({"content": content, "role": role})
                
        return input_messages

    @staticmethod
    def _extract_output_message(response):
        """Extract output messages from the stored response.
        Anthropic allows for chat messages, which requires some special casing.
        Bedrock's Converse API returns a different response format.
        """
        # Handle Converse API response format with output field structure
        if "output" in response and "message" in response.get("output", {}):
            message = response.get("output", {}).get("message", {})
            role = message.get("role", "assistant")
            
            # Handle structured content array
            if message.get("content") and isinstance(message["content"], list):
                content_texts = []
                for content_part in message["content"]:
                    # Handle text content parts
                    if content_part.get("type", "") == "text" or "text" in content_part:
                        content_texts.append(content_part.get("text", ""))
                # Join all text content parts
                content = " ".join(content_texts)
                return [{"content": content, "role": role}]
            # Handle string content
            elif isinstance(message.get("content", ""), str):
                return [{"content": message.get("content", ""), "role": role}]
        
        # Handle older Converse API format
        elif "message" in response:
            # Converse API response
            message = response.get("message", {})
            content = message.get("content", "")
            role = message.get("role", "assistant")
            return [{"content": content, "role": role}]
            
        # Standard InvokeModel API response format
        if isinstance(response.get("text", ""), str):
            return [{"content": response["text"]}]
        if isinstance(response.get("text", []), list):
            if isinstance(response["text"][0], str):
                return [{"content": str(content)} for content in response["text"]]
            if isinstance(response["text"][0], dict):
                return [{"content": response["text"][0].get("text", "")}]
                
        # Default empty response if format is not recognized
        return [{"content": ""}]
