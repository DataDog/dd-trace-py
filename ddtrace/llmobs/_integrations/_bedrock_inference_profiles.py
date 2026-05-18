"""Process-local cache mapping AWS Bedrock application-inference-profile ARNs to
their underlying base model ids.

Populated by the langchain integration when it sees a ChatBedrockConverse instance
that carries both `model_id` (the profile ARN) and `base_model_id` (the underlying
model), and consulted by the botocore integration when it processes a Bedrock API
call whose `modelId` is an application-inference-profile ARN. This lets the
botocore span report the resolved base model without having to call
`bedrock:GetInferenceProfile` or construct its own boto3 client.
"""
import threading
from typing import Optional


_INFERENCE_PROFILE_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()


def record_inference_profile(profile_arn: str, base_model_id: str) -> None:
    if not profile_arn or not base_model_id:
        return
    with _CACHE_LOCK:
        _INFERENCE_PROFILE_CACHE[profile_arn] = base_model_id


def lookup_inference_profile(profile_arn: str) -> Optional[str]:
    if not profile_arn:
        return None
    with _CACHE_LOCK:
        return _INFERENCE_PROFILE_CACHE.get(profile_arn)


def _clear_inference_profile_cache() -> None:
    with _CACHE_LOCK:
        _INFERENCE_PROFILE_CACHE.clear()
