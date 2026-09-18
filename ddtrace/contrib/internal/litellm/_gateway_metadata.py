"""Allowlisted route and pricing inputs, without retaining request content or credentials."""

from collections.abc import Mapping
from itertools import islice
import re
from typing import Any
from typing import Optional
from urllib.parse import urlsplit

from ddtrace.contrib.internal.litellm._gateway_usage import get
from ddtrace.contrib.internal.litellm._gateway_usage import label


_PROVIDERS = {
    "openai": ("openai", "api"),
    "anthropic": ("anthropic", "platform-api"),
    "azure": ("azure", "foundry"),
    "azure_ai": ("azure", "foundry"),
    "bedrock": ("aws", "bedrock"),
    "vertex_ai": ("gcp", "vertex-ai"),
    "gemini": ("gcp", "gemini-api"),
    "oci": ("oracle", "generative-ai"),
}
_VERTEX_HOST = re.compile(r"(?:[a-z0-9-]+-)?aiplatform\.googleapis\.com")
_OCI_HOST = re.compile(r"inference\.generativeai\.[a-z0-9-]+\.oci\.oraclecloud\.com")
_BEDROCK_RESOURCE = re.compile(
    r"arn:(?:aws|aws-us-gov|aws-cn):bedrock:(?P<region>[a-z0-9-]+):(?P<account>[0-9]{12})?:"
    r"(?:application-inference-profile|inference-profile|provisioned-model|imported-model|custom-model-deployment|"
    r"foundation-model)/[a-zA-Z0-9_.:/-]+"
)
_VERTEX_TRAFFIC_MODES = {
    "ON_DEMAND": "standard",
    "ON_DEMAND_PRIORITY": "priority",
    "ON_DEMAND_FLEX": "flex",
    "PROVISIONED_THROUGHPUT": "provisioned_throughput",
}
# Keep this explicit: an arbitrary subdomain does not establish the billing provider.
# Regional endpoints: https://developers.openai.com/api/docs/guides/your-data
_OPENAI_REGIONAL_HOSTS = {
    f"{region}.api.openai.com": region for region in ("us", "eu", "au", "ca", "jp", "in", "sg", "kr", "gb", "ae")
}
_ENUMS = {
    "service_tier": {"auto", "default", "standard", "priority", "flex", "scale"},
    "speed": {"standard", "fast"},
    "reasoning_effort": {"none", "minimal", "low", "medium", "high", "xhigh", "max"},
    "quality": {"auto", "low", "medium", "high", "standard", "hd"},
    "size": {"auto", "256x256", "512x512", "1024x1024", "1024x1536", "1536x1024", "1792x1024", "1024x1792"},
    "prompt_cache_retention": {"in_memory", "24h"},
    "inference_geo": {"us", "global"},
}


def request_tags(data: Any, prefix: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for key, allowed in _ENUMS.items():
        value = get(data, key)
        if isinstance(value, str) and value in allowed:
            tags[f"{prefix}.{key}"] = value
    effort = get(get(data, "reasoning"), "effort")
    if isinstance(effort, str) and effort in _ENUMS["reasoning_effort"]:
        tags[f"{prefix}.reasoning_effort"] = effort
    for key in ("n", "dimensions", "max_tokens", "max_completion_tokens", "max_output_tokens"):
        value = get(data, key)
        if type(value) is int and 0 <= value <= 2**53:
            tags[f"{prefix}.{key}"] = str(value)
    # Native provider spellings appear after LiteLLM's request transformation.
    tier = get(data, "serviceTier")
    if isinstance(tier, Mapping):
        tier = tier.get("type")
    if isinstance(tier, str) and tier in _ENUMS["service_tier"]:
        tags[f"{prefix}.service_tier"] = tier
    for parent in ("performanceConfig", "performance_config"):
        latency = get(get(data, parent), "latency")
        if isinstance(latency, str) and latency in ("standard", "optimized"):
            tags[f"{prefix}.performance_latency"] = latency
    search_context = get(get(data, "web_search_options"), "search_context_size")
    if isinstance(search_context, str) and search_context in ("low", "medium", "high"):
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
            for key, kind in (("cache_control", "ephemeral"), ("cachePoint", "default")):
                control = node.get(key)
                if isinstance(control, Mapping) and control.get("type") == kind:
                    ttl = control.get("ttl", "5m")
                    ttls.add(ttl if isinstance(ttl, str) and ttl in ("5m", "1h") else "unknown")
            for key in ("messages", "input", "system", "tools", "toolConfig", "content"):
                child = node.get(key)
                if isinstance(child, (Mapping, list)):
                    if visited + len(pending) < 512:
                        pending.append((child, depth + 1))
                    else:
                        truncated = True
    tags = {f"{prefix}.prompt_cache_ttls": ",".join(sorted(ttls))} if ttls else {}
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
        if not provider and "/" in model and model.split("/", 1)[0] in _PROVIDERS:
            provider = model.split("/", 1)[0]
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
    ):
        if value := label(get(data, key)) or previous.get(f"ai.route.{key}"):
            tags[f"ai.route.{key}"] = value
    if provider == "bedrock":
        # This is the provider's modelId, NOT _hidden_params.model_id (router deployment).
        model_id = label(get(data, "model_id"), 2048) if "model_id" in data else previous.get("ai.route.model_id")
        if model_id:
            tags["ai.route.model_id"] = model_id
        resource = model_id or (model or "").removeprefix("bedrock/").removeprefix("converse/").removeprefix("invoke/")
        match = _BEDROCK_RESOURCE.fullmatch(resource)
        if match:
            tags["ai.route.resource_id"] = resource
            tags["ai.route.resource_region"] = match["region"]
            if match["account"]:
                # Resource ownership does not establish the caller's billed account.
                tags["ai.route.resource_owner_account_id"] = match["account"]
    if provider == "oci":
        for key in ("oci_tenancy", "oci_compartment_id", "oci_region"):
            if value := label(get(data, key)) or previous.get(f"ai.route.{key}"):
                tags[f"ai.route.{key}"] = value
    # Only the provider pre-call hook supplies headers, never ingress request headers.
    # Select non-secret OpenAI scope IDs without retaining authorization or other headers.
    if provider == "openai" and "ai.route.project" in previous:
        tags["ai.route.project"] = previous["ai.route.project"]
    if provider == "openai" and isinstance(headers, Mapping):
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
            if provider == "openai" and host in _OPENAI_REGIONAL_HOSTS:
                tags["ai.route.endpoint_region"] = _OPENAI_REGIONAL_HOSTS[host]
    # A provider adapter can point at another gateway. Only known endpoints/defaults
    # identify the billing provider; never assume every OpenAI-compatible API is OpenAI.
    official = endpoint is None or (
        host is not None
        and (
            (provider == "openai" and (host == "api.openai.com" or host in _OPENAI_REGIONAL_HOSTS))
            or (provider == "anthropic" and host == "api.anthropic.com")
            or (
                provider in ("azure", "azure_ai")
                and host.endswith((".openai.azure.com", ".services.ai.azure.com", ".models.ai.azure.com"))
            )
            or (provider == "bedrock" and host.startswith("bedrock-runtime.") and host.endswith(".amazonaws.com"))
            or (provider == "vertex_ai" and _VERTEX_HOST.fullmatch(host) is not None)
            or (provider == "gemini" and host == "generativelanguage.googleapis.com")
            or (provider == "oci" and _OCI_HOST.fullmatch(host) is not None)
        )
    )
    if provider in _PROVIDERS and official:
        billing_provider, product = _PROVIDERS[provider]
        tags.update({"ai.billing.provider": billing_provider, "ai.billing.product": product})
        tags["ai.billing.provider_source"] = "selected_route"
        if provider == "openai" and "ai.route.organization" in tags:
            tags["ai.billing.account_id"] = tags["ai.route.organization"]
        if provider == "openai" and "ai.route.project" in tags:
            tags["ai.billing.project_id"] = tags["ai.route.project"]
        if provider == "vertex_ai" and "ai.route.vertex_project" in tags:
            tags["ai.billing.project_id"] = tags["ai.route.vertex_project"]
        if provider == "bedrock" and "ai.route.resource_id" in tags:
            tags["ai.billing.resource_id"] = tags["ai.route.resource_id"]
        if provider == "oci":
            # The signer's tenancy is useful join evidence, but cross-tenancy access
            # means it is not necessarily the account billed for the resource.
            if "ai.route.oci_compartment_id" in tags:
                tags["ai.billing.project_id"] = tags["ai.route.oci_compartment_id"]
    return tags


def response_tags(response: Any, provider: Optional[str]) -> dict[str, str]:
    """Retain explicit pricing and correlation scalars, never raw response metadata."""
    tags: dict[str, str] = {}
    usage = get(response, "usage")
    tier = label(get(response, "service_tier")) or label(get(usage, "service_tier"))
    if tier and tier != "auto":
        tags["ai.observed.service_tier"] = tier
        tags["ai.billing.mode"] = tier
        tags["ai.billing.mode_source"] = "response_service_tier"
    hidden = get(response, "_hidden_params")
    if provider in ("vertex_ai", "gemini"):
        traffic = get(get(hidden, "provider_specific_fields"), "traffic_type")
        if isinstance(traffic, str) and traffic in _VERTEX_TRAFFIC_MODES:
            tags["ai.observed.traffic_type"] = traffic
            tags["ai.billing.mode"] = _VERTEX_TRAFFIC_MODES[traffic]
            tags["ai.billing.mode_source"] = "response_traffic_type"
    for key in ("speed", "inference_geo"):
        if value := label(get(usage, key)):
            tags[f"ai.observed.{key}"] = value
    # LiteLLM prefixes retained upstream headers with llm_provider-. Never read
    # caller headers, nor export provider-specific fields containing reasoning/content.
    headers = get(hidden, "additional_headers")
    if isinstance(headers, Mapping) and len(headers) <= 128:
        selected: dict[str, set[Optional[str]]] = {}
        for header, raw_value in headers.items():
            if not isinstance(header, str):
                continue
            key = header.lower().removeprefix("llm_provider-")
            if key in ("x-request-id", "request-id", "x-amzn-requestid", "apim-request-id", "opc-request-id"):
                selected.setdefault(key, set()).add(label(raw_value))
        for key, values in selected.items():
            if len(values) == 1 and (value := next(iter(values))) is not None:
                tags[f"ai.response.{key.replace('-', '_')}"] = value
    return tags
