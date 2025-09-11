"""Data extraction utilities for vLLM integration."""

from typing import Optional, Tuple, Any, List
from vllm.outputs import PoolingRequestOutput, RequestOutput


class RequestData:
    """Structured data extracted from vLLM requests and responses."""
    
    def __init__(
        self,
        request_id: Optional[str] = None,
        prompt: Optional[str] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        output_text: str = "",
        finish_reason: Optional[str] = None,
        stop_reason: Optional[Any] = None,
        embedding_dim: Optional[int] = None,
        num_cached_tokens: Optional[int] = None,
        model_name: Optional[str] = None,
        lora_name: Optional[str] = None,
        sampling_params: Optional[Any] = None,
    ):
        self.request_id = request_id
        self.prompt = prompt
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.output_text = output_text
        self.finish_reason = finish_reason
        self.stop_reason = stop_reason
        self.embedding_dim = embedding_dim
        self.num_cached_tokens = num_cached_tokens
        self.model_name = model_name
        self.lora_name = lora_name
        self.sampling_params = sampling_params


def extract_v0_data(res: RequestOutput, seq_group) -> RequestData:
    """Extract data from v0 RequestOutput and SequenceGroup."""
    data = RequestData()
    
    # Basic fields
    data.request_id = getattr(res, "request_id", None)
    data.num_cached_tokens = getattr(res, "num_cached_tokens", None)
    
    # Extract prompt with fallbacks
    data.prompt = getattr(res, "prompt", None)
    if not data.prompt and seq_group:
        data.prompt = getattr(seq_group, "prompt", None) or getattr(seq_group, "encoder_prompt", None)
    
    # Extract input tokens from seq_group (more reliable than res for DELTA mode)
    if seq_group:
        sg_prompt_ids = getattr(seq_group, "prompt_token_ids", None) or []
        sg_enc_prompt_ids = getattr(seq_group, "encoder_prompt_token_ids", None) or []
        data.input_tokens = len(sg_prompt_ids) + len(sg_enc_prompt_ids)
        
        data.sampling_params = getattr(seq_group, "sampling_params", None)
        lora_req = getattr(seq_group, "lora_request", None)
        data.lora_name = getattr(lora_req, "name", None) if lora_req else None
    
    # Fallback for input tokens if seq_group didn't provide them
    if data.input_tokens == 0:
        prompt_token_ids = getattr(res, "prompt_token_ids", None) or []
        enc_prompt_token_ids = getattr(res, "encoder_prompt_token_ids", None) or []
        data.input_tokens = len(prompt_token_ids) + len(enc_prompt_token_ids)
    
    # Handle different output types
    if isinstance(res, PoolingRequestOutput):
        # Embedding output
        output_data = getattr(getattr(res, "outputs", None), "data", None)
        if output_data and hasattr(output_data, "shape") and len(output_data.shape) >= 1:
            data.embedding_dim = int(output_data.shape[-1])
    else:
        # Completion output - extract from sequences for full text
        if seq_group and hasattr(seq_group, "get_seqs"):
            seqs = seq_group.get_seqs() or []
            output_parts = []
            for seq in seqs:
                if hasattr(seq, "get_output_len"):
                    data.output_tokens += int(seq.get_output_len())
                text = getattr(seq, "output_text", None)
                if text:
                    output_parts.append(text)
            data.output_text = "".join(output_parts)
        
        # Extract finish/stop reasons from completion outputs
        for comp in getattr(res, "outputs", None) or []:
            if data.output_tokens == 0:
                token_ids = getattr(comp, "token_ids", None)
                if token_ids:
                    data.output_tokens += len(token_ids) if isinstance(token_ids, list) else 1
            
            if not data.finish_reason:
                data.finish_reason = getattr(comp, "finish_reason", None)
            
            if data.stop_reason is None:
                data.stop_reason = getattr(comp, "stop_reason", None)
    
    return data


def extract_v1_streaming_data(outputs: List[RequestOutput]) -> RequestData:
    """Extract accumulated data from v1 streaming outputs."""
    data = RequestData()
    
    for out in outputs:
        # Get prompt from first output that has it
        if not data.prompt:
            data.prompt = getattr(out, "prompt", None) or getattr(out, "encoder_prompt", None)
        
        # Get input tokens from first output
        if not data.input_tokens:
            prompt_token_ids = getattr(out, "prompt_token_ids", None)
            if prompt_token_ids:
                data.input_tokens = len(prompt_token_ids)
        
        # Get cached tokens
        if data.num_cached_tokens is None:
            data.num_cached_tokens = getattr(out, "num_cached_tokens", None)
        
        # Accumulate output data
        for comp in getattr(out, "outputs", None) or []:
            text = getattr(comp, "text", None)
            if text:
                data.output_text += text
            
            token_ids = getattr(comp, "token_ids", None)
            if token_ids:
                data.output_tokens += len(token_ids) if isinstance(token_ids, list) else 1
            
            # Get final states
            finish_reason = getattr(comp, "finish_reason", None)
            if finish_reason:
                data.finish_reason = finish_reason
            
            stop_reason = getattr(comp, "stop_reason", None)
            if stop_reason is not None:
                data.stop_reason = stop_reason
    
    return data


def extract_offline_data(request_output: RequestOutput, prompts=None, model_name=None) -> RequestData:
    """Extract data from offline LLM.generate RequestOutput."""
    data = RequestData()
    
    data.request_id = getattr(request_output, "request_id", None)
    data.prompt = getattr(request_output, "prompt", None)
    data.model_name = model_name
    data.num_cached_tokens = getattr(request_output, "num_cached_tokens", None)
    
    # Input tokens
    prompt_token_ids = getattr(request_output, "prompt_token_ids", None) or []
    data.input_tokens = len(prompt_token_ids)
    
    # Output data
    output_parts = []
    for comp in getattr(request_output, "outputs", None) or []:
        text = getattr(comp, "text", None)
        if text:
            output_parts.append(text)
        
        token_ids = getattr(comp, "token_ids", None)
        if token_ids:
            data.output_tokens += len(token_ids) if isinstance(token_ids, list) else 1
        
        if not data.finish_reason:
            data.finish_reason = getattr(comp, "finish_reason", None)
        
        if data.stop_reason is None:
            data.stop_reason = getattr(comp, "stop_reason", None)
    
    data.output_text = "".join(output_parts)
    
    # Fallback prompt from input if not in response
    if not data.prompt and isinstance(prompts, str):
        data.prompt = prompts
    
    # LoRA info
    lora_req = getattr(request_output, "lora_request", None)
    data.lora_name = getattr(lora_req, "name", None) if lora_req else None
    
    return data


def extract_captured_prompt(parent_span) -> Optional[str]:
    """Extract captured prompt from parent span context."""
    if not parent_span:
        return None
    return parent_span._get_ctx_item("vllm.captured_prompt")


def extract_model_name(instance) -> Optional[str]:
    """Extract model name from vLLM instance."""
    model_config = getattr(instance, "model_config", None)
    return getattr(model_config, "model", None) if model_config else None


def extract_lora_name(lora_request) -> Optional[str]:
    """Extract LoRA name from request."""
    if not lora_request:
        return None
    return getattr(lora_request, "lora_name", None) or getattr(lora_request, "name", None)
