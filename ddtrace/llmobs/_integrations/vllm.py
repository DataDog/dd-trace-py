from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    try:
        from vllm.sequence import SequenceGroup
    except ImportError:
        # vllm may not be installed in development environments
        from typing import Any as SequenceGroup

from ddtrace.llmobs._constants import (
    INPUT_MESSAGES,
    INPUT_TOKENS_METRIC_KEY,
    METADATA,
    METRICS,
    MODEL_NAME,
    MODEL_PROVIDER,
    OUTPUT_MESSAGES,
    OUTPUT_TOKENS_METRIC_KEY,
    SPAN_KIND,
    TOTAL_TOKENS_METRIC_KEY,
)
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _get_attr
from ddtrace.trace import Span
from ddtrace.internal.logger import get_logger

log = get_logger(__name__)


class VLLMIntegration(BaseLLMIntegration):
    _integration_name = "vllm"

    def _set_base_span_tags(self, span: Span, **kwargs) -> None:
        """Set base level tags that should be present on all vLLM spans."""
        # Set the model name if available from the engine config
        model_name = kwargs.get("model_name")
        if model_name:
            span.set_tag_str("vllm.request.model", model_name)

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Extract comprehensive LLMObs tags from vLLM SequenceGroup and set them on the span.
        
        This method extracts detailed information from the completed vLLM request including:
        - Model metadata (name, provider)
        - Input/output messages formatted for LLM observability
        - Sampling parameters (temperature, max_tokens, etc.)
        - Token usage metrics (input, output, total tokens)
        """
        log.debug("[VLLM INTEGRATION DEBUG] _llmobs_set_tags called with response type: %s", type(response).__name__ if response else "None")
        
        # The response should be a SequenceGroup from vLLM
        seq_group = response
        if not seq_group:
            log.debug("[VLLM INTEGRATION DEBUG] No seq_group provided, returning early")
            return
            
        # Get model name from kwargs or span tags
        model_name = kwargs.get("model_name") or span.get_tag("vllm.request.model") or ""
        log.debug("[VLLM INTEGRATION DEBUG] Using model_name: %s", model_name)
        
        # Extract sampling parameters for metadata
        log.debug("[VLLM INTEGRATION DEBUG] Extracting sampling metadata")
        metadata = self._extract_sampling_metadata(seq_group)
        log.debug("[VLLM INTEGRATION DEBUG] Extracted metadata: %s", metadata)
        
        # Extract input information
        log.debug("[VLLM INTEGRATION DEBUG] Extracting input data")
        input_data = self._extract_input_data(seq_group)
        log.debug("[VLLM INTEGRATION DEBUG] Extracted input_data: %s", input_data)
        
        # Extract output information
        log.debug("[VLLM INTEGRATION DEBUG] Extracting output data")
        output_data = self._extract_output_data(seq_group)
        log.debug("[VLLM INTEGRATION DEBUG] Extracted output_data: %s", output_data)
        
        # Calculate token metrics
        log.debug("[VLLM INTEGRATION DEBUG] Extracting token metrics")
        metrics = self._extract_token_metrics(seq_group)
        log.debug("[VLLM INTEGRATION DEBUG] Extracted metrics: %s", metrics)
        
        # Set LLMObs context items
        ctx_items = {
            SPAN_KIND: "llm",
            MODEL_NAME: model_name,
            MODEL_PROVIDER: "vllm",
            METADATA: metadata,
            METRICS: metrics,
            **input_data,
            **output_data,
        }
        log.debug("[VLLM INTEGRATION DEBUG] Setting ctx_items: %s", ctx_items)
        span._set_ctx_items(ctx_items)
        log.debug("[VLLM INTEGRATION DEBUG] LLMObs tags set successfully")

    def _extract_sampling_metadata(self, seq_group: "SequenceGroup") -> Dict[str, Any]:
        """Extract sampling parameters from sequence group."""
        metadata = {}
        
        if hasattr(seq_group, 'sampling_params') and seq_group.sampling_params:
            params = seq_group.sampling_params
            
            # Extract key sampling parameters
            if hasattr(params, 'temperature') and params.temperature is not None:
                metadata["temperature"] = params.temperature
            if hasattr(params, 'max_tokens') and params.max_tokens is not None:
                metadata["max_tokens"] = params.max_tokens
            if hasattr(params, 'top_p') and params.top_p is not None:
                metadata["top_p"] = params.top_p
            if hasattr(params, 'top_k') and params.top_k is not None and params.top_k != -1:
                metadata["top_k"] = params.top_k
            if hasattr(params, 'n') and params.n is not None:
                metadata["n"] = params.n
            if hasattr(params, 'presence_penalty') and params.presence_penalty is not None:
                metadata["presence_penalty"] = params.presence_penalty
            if hasattr(params, 'frequency_penalty') and params.frequency_penalty is not None:
                metadata["frequency_penalty"] = params.frequency_penalty
            if hasattr(params, 'repetition_penalty') and params.repetition_penalty is not None:
                metadata["repetition_penalty"] = params.repetition_penalty
            if hasattr(params, 'seed') and params.seed is not None:
                metadata["seed"] = params.seed
                
        return metadata

    def _extract_input_data(self, seq_group: "SequenceGroup") -> Dict[str, Any]:
        """Extract input data from sequence group.
        
        For vLLM, we extract the prompt text and format it as INPUT_MESSAGES
        following the LLM integration pattern used by OpenAI, Anthropic, etc.
        """
        input_data = {}
        
        # Extract prompt text from trace headers (stored during generate call)
        prompt_text = None
        
        # Method 1: Get prompt from trace headers (preferred - captured at entry point)
        trace_headers = getattr(seq_group, 'trace_headers', {})
        if trace_headers and isinstance(trace_headers, dict):
            prompt_text = trace_headers.get("x-datadog-vllm-prompt")
        
        # Method 2: Fallback to SequenceGroup attributes (may be None after tokenization)
        if not prompt_text and hasattr(seq_group, 'prompt'):
            prompt_text = getattr(seq_group, 'prompt', None)
        
        # Method 3: Fallback to first_seq.inputs.prompt
        if not prompt_text and hasattr(seq_group, 'first_seq'):
            first_seq = seq_group.first_seq
            if first_seq and hasattr(first_seq, 'inputs'):
                inputs = first_seq.inputs
                if inputs and hasattr(inputs, 'prompt'):
                    prompt_text = getattr(inputs, 'prompt', None)
                
                # Fallback 3: Try to decode token IDs if tokenizer is available
        if not prompt_text:
            try:
                # Try to get token IDs from the sequence
                if hasattr(seq_group, 'first_seq') and hasattr(seq_group.first_seq, 'inputs'):
                    inputs = seq_group.first_seq.inputs
                    if hasattr(inputs, 'prompt_token_ids') and inputs.prompt_token_ids:
                        # Try to access tokenizer through various paths
                        tokenizer = None
                        
                        # Check if we can get tokenizer from the engine
                        if hasattr(seq_group, 'engine') and hasattr(seq_group.engine, 'tokenizer'):
                            tokenizer = seq_group.engine.tokenizer
                        elif hasattr(seq_group, 'tokenizer'):
                            tokenizer = seq_group.tokenizer
                        
                        if tokenizer:
                            prompt_text = tokenizer.decode(inputs.prompt_token_ids, skip_special_tokens=True)
                            log.debug("[VLLM INTEGRATION DEBUG] Decoded prompt from tokens: %s", prompt_text[:100] if prompt_text else "None")
                        else:
                            log.debug("[VLLM INTEGRATION DEBUG] No tokenizer found for token decoding")
            except Exception as e:
                log.debug("[VLLM INTEGRATION DEBUG] Failed to decode tokens: %s", str(e))
        
        if prompt_text:
            # Format as INPUT_MESSAGES following LLM integration standards
            # For completion-style prompts, we treat them as user messages
            input_data[INPUT_MESSAGES] = [{"content": prompt_text, "role": "user"}]
        
        return input_data

    def _extract_output_data(self, seq_group: "SequenceGroup") -> Dict[str, Any]:
        """Extract output data from sequence group.
        
        Collects the generated text from all sequences in the group and formats
        it as OUTPUT_MESSAGES following LLM integration standards.
        """
        output_data = {}
        
        if hasattr(seq_group, 'seqs') and seq_group.seqs:
            # Collect output text from all sequences
            outputs = []
            for seq in seq_group.seqs:
                if hasattr(seq, 'output_text') and seq.output_text:
                    outputs.append(seq.output_text)
                elif hasattr(seq, 'get_output_text'):
                    # Some versions use a method
                    try:
                        text = seq.get_output_text()
                        if text:
                            outputs.append(text)
                    except Exception:
                        pass
            
            if outputs:
                # Format as OUTPUT_MESSAGES following LLM integration standards
                if len(outputs) == 1:
                    output_data[OUTPUT_MESSAGES] = [{"content": outputs[0], "role": "assistant"}]
                else:
                    # For multiple outputs, combine them into a single assistant message
                    combined_output = "\n".join(f"Output {i+1}: {out}" for i, out in enumerate(outputs))
                    output_data[OUTPUT_MESSAGES] = [{"content": combined_output, "role": "assistant"}]
        
        return output_data

    def _extract_token_metrics(self, seq_group: "SequenceGroup") -> Dict[str, Any]:
        """Extract token usage metrics from sequence group.
        
        Calculates input tokens (prompt) and output tokens (generated) to provide
        comprehensive token usage information.
        """
        metrics = {}
        
        # Calculate input tokens from prompt
        input_tokens = 0
        if hasattr(seq_group, 'prompt_token_ids') and seq_group.prompt_token_ids:
            input_tokens = len(seq_group.prompt_token_ids)
        
        # Calculate output tokens from all sequences
        output_tokens = 0
        if hasattr(seq_group, 'seqs') and seq_group.seqs:
            for seq in seq_group.seqs:
                if hasattr(seq, 'output_token_ids') and seq.output_token_ids:
                    output_tokens += len(seq.output_token_ids)
                elif hasattr(seq, 'get_output_token_ids'):
                    try:
                        token_ids = seq.get_output_token_ids()
                        if token_ids:
                            output_tokens += len(token_ids)
                    except Exception:
                        pass
        
        # Set metrics if we have token counts
        if input_tokens > 0:
            metrics[INPUT_TOKENS_METRIC_KEY] = input_tokens
        if output_tokens > 0:
            metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
        if input_tokens > 0 or output_tokens > 0:
            metrics[TOTAL_TOKENS_METRIC_KEY] = input_tokens + output_tokens
        
        return metrics
