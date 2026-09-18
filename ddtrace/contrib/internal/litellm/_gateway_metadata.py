"""Raw route and pricing fields, without retaining request content or credentials."""

from collections.abc import Mapping
from itertools import islice
from typing import Any
from typing import Optional
from urllib.parse import urlsplit

from ddtrace.contrib.internal.litellm._gateway_usage import get
from ddtrace.contrib.internal.litellm._gateway_usage import label


def request_tags(data: Any, prefix: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for key in (
        "service_tier",
        "speed",
        "reasoning_effort",
        "quality",
        "size",
        "prompt_cache_retention",
        "inference_geo",
    ):
        if setting := label(get(data, key)):
            tags[f"{prefix}.{key}"] = setting
    if effort := label(get(get(data, "reasoning"), "effort")):
        tags[f"{prefix}.reasoning_effort"] = effort
    for key in ("n", "dimensions", "max_tokens", "max_completion_tokens", "max_output_tokens"):
        value = get(data, key)
        if type(value) is int and 0 <= value <= 2**53:
            tags[f"{prefix}.{key}"] = str(value)
    # Native provider spellings appear after LiteLLM's request transformation.
    tier = get(data, "serviceTier")
    if isinstance(tier, Mapping):
        tier = tier.get("type")
    if tier := label(tier):
        tags[f"{prefix}.service_tier"] = tier
    for parent in ("performanceConfig", "performance_config"):
        if latency := label(get(get(data, parent), "latency")):
            tags[f"{prefix}.performance_latency"] = latency
    if search_context := label(get(get(data, "web_search_options"), "search_context_size")):
        tags[f"{prefix}.web_search_context_size"] = search_context
    generation = get(data, "generationConfig")
    for key, value in (
        ("thinking_budget_tokens", get(get(data, "thinking"), "budget_tokens")),
        ("thinking_budget_tokens", get(get(generation, "thinkingConfig"), "thinkingBudget")),
        ("max_output_tokens", get(generation, "maxOutputTokens")),
    ):
        if type(value) is int and 0 <= value <= 2**53:
            tags[f"{prefix}.{key}"] = str(value)
    return tags


def cache_tags(data: Any, prefix: str) -> dict[str, str]:
    # Traverse structure only. Never stringify content, tools, headers, or metadata.
    # Bound work even for huge prompts and cyclic programmatic inputs.
    pending = [(data, 0)]
    ttls: set[str] = set()
    types: set[str] = set()
    unspecified_ttl = False
    visited = 0
    truncated = False
    while pending and visited < 512:
        node, depth = pending.pop()
        visited += 1
        if depth > 12:
            truncated = True
            continue
        if isinstance(node, list):
            room = max(0, 512 - visited - len(pending))
            truncated |= len(node) > room
            pending.extend((item, depth + 1) for item in islice(node, room))
        elif isinstance(node, Mapping):
            for key in ("cache_control", "cachePoint"):
                control = node.get(key)
                if isinstance(control, Mapping):
                    if kind := label(control.get("type")):
                        types.add(kind)
                    if ttl := label(control.get("ttl")):
                        ttls.add(ttl)
                    if "ttl" not in control:
                        unspecified_ttl = True
            for key in ("messages", "input", "system", "tools", "toolConfig", "content"):
                child = node.get(key)
                if isinstance(child, (Mapping, list)):
                    if visited + len(pending) < 512:
                        pending.append((child, depth + 1))
                    else:
                        truncated = True
    tags = {f"{prefix}.prompt_cache_ttls": ",".join(sorted(ttls))} if ttls else {}
    if types:
        tags[f"{prefix}.prompt_cache_types"] = ",".join(sorted(types))
    if unspecified_ttl:
        tags[f"{prefix}.prompt_cache_ttl_unspecified"] = "true"
    if truncated or pending:
        tags[f"{prefix}.prompt_cache_scan"] = "incomplete"
    return tags


def route_tags(data: Any, previous: Optional[dict[str, str]] = None, *, headers: Any = None) -> dict[str, str]:
    tags: dict[str, str] = {}
    previous = previous or {}
    model = label(get(data, "model"), 2048) or previous.get("ai.route.model")
    provider = label(get(data, "custom_llm_provider")) or previous.get("ai.route.provider")
    if model:
        tags["ai.route.model"] = model
    if provider:
        tags["ai.route.provider"] = provider
    for key in (
        "vertex_project",
        "vertex_location",
        "region_name",
        "aws_region_name",
        "aws_bedrock_project_id",
        "organization",
        "api_version",
        "oci_tenancy",
        "oci_compartment_id",
        "oci_region",
    ):
        if value := label(get(data, key)) or previous.get(f"ai.route.{key}"):
            tags[f"ai.route.{key}"] = value
    # Preserve provider model/resource identifiers without interpreting ARN or billing semantics.
    for key in ("model_id", "resource_id"):
        value = label(get(data, key), 2048) if key in data else previous.get(f"ai.route.{key}")
        if value:
            tags[f"ai.route.{key}"] = value
    # Only the provider pre-call hook supplies headers, never ingress request headers.
    # Select non-secret OpenAI scope IDs without retaining authorization or other headers.
    for key in ("ai.route.project", "ai.route.api_key_id"):
        if key in previous:
            tags[key] = previous[key]
    if isinstance(headers, Mapping):
        scope: dict[str, set[Optional[str]]] = {}
        if len(headers) <= 128:
            for key, value in headers.items():
                if isinstance(key, str) and key.lower() in ("openai-organization", "openai-project"):
                    scope.setdefault(key.lower().removeprefix("openai-"), set()).add(label(value))
        else:
            scope = {"organization": {None}, "project": {None}}
        for key, values in scope.items():
            value = next(iter(values)) if len(values) == 1 else None
            if value is not None:
                tags[f"ai.route.{key}"] = value
            else:
                tags.pop(f"ai.route.{key}", None)
    endpoint = get(data, "api_base") or get(data, "base_url") or get(data, "aws_bedrock_runtime_endpoint")
    if not endpoint and previous.get("ai.route.endpoint_host"):
        endpoint = "https://" + previous["ai.route.endpoint_host"]
    host = None
    if endpoint is not None:
        try:
            if isinstance(endpoint, str):
                parsed = urlsplit(endpoint)
                if parsed.scheme in ("http", "https"):
                    host = label(parsed.hostname)
            elif get(endpoint, "scheme") in ("http", "https"):
                # LiteLLM's OpenAI adapter also exposes parsed httpx URL objects.
                # Never stringify them: paths, queries and userinfo can contain secrets.
                host = label(get(endpoint, "host"))
        except ValueError:
            pass
        if host:
            host = host.lower()
            tags["ai.route.endpoint_host"] = host
    return tags


def response_tags(response: Any, *, provider_response: Any = None) -> dict[str, str]:
    """Retain explicit pricing and correlation scalars, never whole response metadata."""
    tags: dict[str, str] = {}
    usage = get(response, "usage")
    tier = label(get(response, "service_tier")) or label(get(usage, "service_tier"))
    if tier:
        tags["ai.observed.service_tier"] = tier
    hidden = get(response, "_hidden_params")
    if traffic := label(get(get(hidden, "provider_specific_fields"), "traffic_type")):
        tags["ai.observed.traffic_type"] = traffic
    for key in ("speed", "inference_geo"):
        if value := label(get(usage, key)):
            tags[f"ai.observed.{key}"] = value
    # LiteLLM prefixes retained upstream headers with llm_provider-. Never read
    # caller headers, nor export provider-specific fields containing reasoning/content.
    # Native Anthropic streaming retains headers on LiteLLM's httpx_response,
    # but not on its rebuilt terminal response. Never read the HTTP body.
    selected: dict[str, set[Optional[str]]] = {}
    for headers in (get(hidden, "additional_headers"), getattr(provider_response, "headers", None)):
        if not isinstance(headers, Mapping) or len(headers) > 128:
            continue
        for header, raw_value in headers.items():
            if not isinstance(header, str):
                continue
            key = header.lower().removeprefix("llm_provider-")
            if key in (
                "x-request-id",
                "request-id",
                "x-amzn-requestid",
                "apim-request-id",
                "opc-request-id",
                "openai-organization",
                "openai-project",
                "anthropic-organization-id",
                "anthropic-workspace-id",
            ):
                selected.setdefault(key, set()).add(label(raw_value))
    for key, values in selected.items():
        if len(values) == 1 and (value := next(iter(values))) is not None:
            tags[f"ai.response.{key.replace('-', '_')}"] = value
    return tags
