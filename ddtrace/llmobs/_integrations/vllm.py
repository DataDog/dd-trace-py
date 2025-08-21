from collections import defaultdict
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Tuple

if TYPE_CHECKING:
    try:
        from vllm.sequence import SequenceGroup
        from vllm.entrypoints.openai.serving_engine import TextTokensPrompt, AnyRequest
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
    
    _prompt_token_ids_to_prompt: Dict[Tuple[int, ...], str] = {}

    def store_prompt_token_ids_to_prompt(self, prompt_token_ids: List[int], prompt: "str") -> None:
        log.debug("[VLLM OPENAI DEBUG] Storing prompt token ids to prompt mapping: %s -> %s", prompt_token_ids, prompt)
        self._prompt_token_ids_to_prompt[tuple(prompt_token_ids)] = prompt
    
    def get_prompt_from_prompt_token_ids(self, prompt_token_ids: List[int]) -> Optional["str"]:
        return self._prompt_token_ids_to_prompt.get(tuple(prompt_token_ids), None)
    
    def clear_prompt_token_ids_to_prompt(self, prompt_token_ids: List[int]) -> None:
        self._prompt_token_ids_to_prompt.pop(tuple(prompt_token_ids), None)
        log.debug("[VLLM OPENAI DEBUG] Cleared prompt token ids to prompt mapping")
    
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
        model_provider = "vllm"
        if "/" in model_name:
            model_provider = model_name.split("/")[0]
            model_name = model_name.split("/")[1]
        ctx_items = {
            SPAN_KIND: "llm",
            MODEL_NAME: model_name,
            MODEL_PROVIDER: model_provider,
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
            metadata["temperature"] = params.temperature
            metadata["max_tokens"] = params.max_tokens
            metadata["top_p"] = params.top_p
            metadata["top_k"] = params.top_k
            metadata["n"] = params.n
            metadata["presence_penalty"] = params.presence_penalty
            metadata["frequency_penalty"] = params.frequency_penalty
            metadata["repetition_penalty"] = params.repetition_penalty
            if hasattr(params, 'seed') and params.seed is not None:
                metadata["seed"] = params.seed
                
        return metadata

    def _extract_input_data(self, seq_group: "SequenceGroup") -> Dict[str, Any]:
        input_data = defaultdict(list)
        for seq in seq_group.seqs:
            log.debug("[VLLM INTEGRATION DEBUG] Extracting input data for seq: %s", seq.inputs)
            # check if seq.inputs["prompt"] is set
            if hasattr(seq.inputs, "prompt"):
                input_data[INPUT_MESSAGES].append({"content": seq.inputs.prompt})
            else:
                prompt = self.get_prompt_from_prompt_token_ids(seq.inputs.prompt_token_ids)
                if prompt is None:
                    log.debug("[VLLM INTEGRATION DEBUG] Prompt not found for prompt token ids: %s", seq.inputs.prompt_token_ids)
                else:
                    input_data[INPUT_MESSAGES].append({"content": prompt})
        return input_data


    def _extract_output_data(self, seq_group: "SequenceGroup") -> Dict[str, Any]:
        output_data = defaultdict(list)
        
        outputs = []
        for seq in seq_group.seqs:
            outputs.append(seq.output_text)
        
        for output in outputs:
            output_data[OUTPUT_MESSAGES].append({"content": output})
        
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
        # TODO: Cached tokens
        if input_tokens > 0:
            metrics[INPUT_TOKENS_METRIC_KEY] = input_tokens
        if output_tokens > 0:
            metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
        if input_tokens > 0 or output_tokens > 0:
            metrics[TOTAL_TOKENS_METRIC_KEY] = input_tokens + output_tokens
        
        return metrics
