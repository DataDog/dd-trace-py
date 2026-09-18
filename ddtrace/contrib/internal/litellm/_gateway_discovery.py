"""Optional provider key lookup. Only selected IDs, never secrets, enter telemetry."""

import asyncio
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
import hashlib
import hmac
import json
import os
import re
import time
from typing import Any
from typing import Optional
from urllib.parse import urlencode

import httpx

from ddtrace.contrib.internal.litellm._gateway_usage import label
from ddtrace.internal import forksafe
from ddtrace.internal.settings.env import dd_environ


_HOSTS = {"anthropic": "api.anthropic.com", "openai": "api.openai.com", "gemini": "apikeys.googleapis.com"}
_TIMEOUT = 3.0
_MAX_REQUESTS = 20
_MAX_BYTES = 1024 * 1024


class _LookupError(Exception):
    pass


@dataclass(frozen=True)
class KeyRequest:
    provider: str
    secret: str = field(repr=False)


def _hint_matches(secret: str, hint: Any) -> bool:
    # A redacted hint is evidence, not a secret or a cryptographic key identifier.
    # Require a prefix AND suffix and never treat arbitrary regex syntax as a pattern.
    if not isinstance(hint, str) or len(hint) > 4096:
        return False
    parts = re.split(r"\.{3}|\u2026|\*+", hint)
    return (
        len(parts) == 2
        and len(parts[0]) >= 3
        and len(parts[1]) >= 3
        and len(secret) > len(parts[0]) + len(parts[1])
        and secret.startswith(parts[0])
        and secret.endswith(parts[1])
    )


def _identifier(value: Any) -> str:
    result = label(value)
    if result is None or re.fullmatch(r"[A-Za-z0-9_-]+", result) is None:
        raise _LookupError("invalid_response")
    return result


class _Inventory:
    def __init__(self, provider: str, credential: str) -> None:
        self.host = _HOSTS[provider]
        self.headers = (
            {"x-api-key": credential, "anthropic-version": "2023-06-01"}
            if provider == "anthropic"
            else {"Authorization": "Bearer " + credential}
        )
        self.remaining = _MAX_REQUESTS

    async def get(self, path: str, **query: str) -> dict[str, Any]:
        self.remaining -= 1
        if self.remaining < 0:
            raise _LookupError("inventory_limit")
        url = "https://" + self.host + path
        if query:
            url += "?" + urlencode(query)
        # AIDEV-NOTE: bypass instrumented Client.send, which could trace administrative
        # headers. Fixed provider hosts, TLS verification, no redirects or env proxies.
        async with httpx.AsyncHTTPTransport(retries=0, trust_env=False) as transport:
            request = httpx.Request(
                "GET",
                url,
                headers=self.headers,
                extensions={"timeout": {key: _TIMEOUT for key in ("connect", "read", "write", "pool")}},
            )
            response = await transport.handle_async_request(request)
            try:
                if response.status_code in (401, 403):
                    raise _LookupError("permission_denied")
                if response.status_code != 200:
                    raise _LookupError("unavailable")
                body = bytearray()
                async for chunk in response.aiter_raw():
                    body.extend(chunk)
                    if len(body) > _MAX_BYTES:
                        raise _LookupError("inventory_limit")
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise _LookupError("invalid_response")
                return result
            finally:
                await response.aclose()

    async def pages(self, path: str, **query: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursors: set[str] = set()
        while True:
            page = await self.get(path, limit="100", **query)
            data = page.get("data")
            if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
                raise _LookupError("invalid_response")
            rows.extend(data)
            if page.get("has_more") is False:
                return rows
            if page.get("has_more") is not True or not data:
                raise _LookupError("invalid_response")
            cursor = _identifier(page.get("last_id"))
            if cursor in cursors:
                raise _LookupError("invalid_response")
            cursors.add(cursor)
            query["after_id" if self.host == _HOSTS["anthropic"] else "after"] = cursor


async def _lookup(request: KeyRequest, credential: str, observed: dict[str, str]) -> dict[str, str]:
    inventory = _Inventory(request.provider, credential)
    if request.provider == "gemini":
        # Google's lookup is exact. Send the key only to its own authenticated API,
        # never to Datadog, a configurable host, or an instrumented HTTP client.
        info = await inventory.get("/v2/keys:lookupKey", keyString=request.secret)
        name = label(info.get("name"), 2048)
        match = re.fullmatch(r"projects/(\d+)/locations/global/keys/([A-Za-z0-9_-]+)", name or "")
        if match is None or info.get("parent") != f"projects/{match[1]}/locations/global":
            raise _LookupError("invalid_response")
        result = {
            "ai.route.api_key_id": match[2],
            "ai.route.api_key_resource_name": name or "",
            "ai.route.project_number": match[1],
            "ai.route.api_key_id_source": "provider_lookup",
            "ai.discovery.status": "discovered",
        }
        # Billing exports commonly use the readable project ID, not its number.
        inventory.host = "cloudresourcemanager.googleapis.com"
        try:
            project_info = await inventory.get(f"/v3/projects/{match[1]}")
            if project_info.get("name") != f"projects/{match[1]}":
                raise _LookupError("invalid_response")
            result["ai.route.project"] = _identifier(project_info.get("projectId"))
        except _LookupError as error:
            result["ai.discovery.project_status"] = str(error)
        return result
    matches: list[dict[str, str]] = []
    if request.provider == "anthropic":
        organization = observed.get("ai.response.anthropic_organization_id")
        if organization:
            info = await inventory.get("/v1/organizations/me")
            if info.get("id") != organization:
                raise _LookupError("scope_mismatch")
        rows = await inventory.pages("/v1/organizations/api_keys")
        for row in rows:
            if _hint_matches(request.secret, row.get("partial_key_hint")):
                key_id = _identifier(row.get("id"))
                scope = row.get("scope")
                workspace = label(scope.get("workspace_id")) if isinstance(scope, dict) else None
                workspace = workspace or label(row.get("workspace_id"))
                expected = observed.get("ai.response.anthropic_workspace_id")
                if workspace and expected and workspace != expected:
                    continue
                matches.append({"ai.route.api_key_id": key_id})
    else:
        if organization := observed.get("ai.response.openai_organization"):
            inventory.headers["OpenAI-Organization"] = organization
        project = observed.get("ai.response.openai_project")
        projects = (
            [{"id": project}]
            if project
            else await inventory.pages("/v1/organization/projects", include_archived="true")
        )
        for row in projects:
            project_id = _identifier(row.get("id"))
            keys = await inventory.pages(f"/v1/organization/projects/{project_id}/api_keys", owner_project_access="any")
            for key in keys:
                if _hint_matches(request.secret, key.get("redacted_value")):
                    matches.append({"ai.route.api_key_id": _identifier(key.get("id")), "ai.route.project": project_id})
    # Inspect the entire inventory: an early match can collide with a later page.
    if len(matches) != 1:
        raise _LookupError("not_found" if not matches else "ambiguous")
    return {**matches[0], "ai.route.api_key_id_source": "unique_key_hint", "ai.discovery.status": "discovered"}


class ProviderKeyDiscovery:
    def __init__(self, providers: Optional[Mapping[str, str]] = None) -> None:
        if providers is None:
            providers = {}
        if not isinstance(providers, Mapping) or any(
            provider not in _HOSTS or not isinstance(env, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env) is None
            for provider, env in providers.items()
        ):
            raise ValueError("Invalid provider key discovery configuration")
        self._providers = dict(providers)
        self._lock = forksafe.Lock()
        self._pid = os.getpid()
        self._salt = os.urandom(32)
        self._cache: OrderedDict[bytes, tuple[float, dict[str, str]]] = OrderedDict()
        self._inflight: set[bytes] = set()

    def capture(
        self, kwargs: dict[str, Any], route: dict[str, str], headers: Optional[Mapping[Any, Any]] = None
    ) -> Optional[KeyRequest]:
        provider = route.get("ai.route.provider", "")
        if provider not in self._providers:
            return None
        host = route.get("ai.route.endpoint_host", "")
        expected = "generativelanguage.googleapis.com" if provider == "gemini" else _HOSTS[provider]
        native = host == expected or (provider == "openai" and host.endswith(".api.openai.com"))
        if not native:
            route["ai.discovery.status"] = "unsupported_endpoint"
            return None
        secret = kwargs.get("api_key")
        # Native Messages can omit api_key in the logger but expose the final
        # upstream auth header. Never read ingress headers or expand env references.
        if headers:
            values: set[str] = set()
            if len(headers) > 128:
                route["ai.discovery.status"] = "ambiguous_api_key"
                return None
            for name, value in headers.items():
                if not isinstance(name, str):
                    continue
                name = name.lower()
                if (
                    name == "authorization"
                    or (provider == "anthropic" and name == "x-api-key")
                    or (provider == "gemini" and name == "x-goog-api-key")
                ):
                    if not isinstance(value, str):
                        route["ai.discovery.status"] = "ambiguous_api_key"
                        return None
                    if name == "authorization":
                        value = value[7:] if value.lower().startswith("bearer ") else ""
                    values.add(value)
            if len(values) > 1:
                route["ai.discovery.status"] = "ambiguous_api_key"
                return None
            if values:
                secret = values.pop()
        prefix = "AIza" if provider == "gemini" else "sk-"
        if not isinstance(secret, str) or not secret.startswith(prefix) or len(secret) > 4096:
            route["ai.discovery.status"] = "missing_api_key"
            return None
        return KeyRequest(provider, secret)

    async def resolve(self, request: Optional[KeyRequest], observed: dict[str, str]) -> dict[str, str]:
        if request is None:
            return {}
        credential = dd_environ.get(self._providers[request.provider])
        if not credential:
            return {"ai.discovery.status": "missing_credential"}
        # Only provider scope affects resolution, never per-request IDs or user identity.
        scope = {
            k: v
            for k, v in observed.items()
            if k
            in (
                "ai.response.anthropic_organization_id",
                "ai.response.anthropic_workspace_id",
                "ai.response.openai_organization",
                "ai.response.openai_project",
            )
        }
        with self._lock:
            pid = os.getpid()
            if self._pid != pid:
                self._cache.clear()
                self._inflight.clear()
                self._salt = os.urandom(32)
                self._pid = pid
            key = hmac.digest(
                self._salt,
                json.dumps([request.provider, request.secret, credential, scope], sort_keys=True).encode(),
                hashlib.sha256,
            )
            cached = self._cache.get(key)
            if cached and cached[0] > time.monotonic():
                self._cache.move_to_end(key)
                return dict(cached[1])
            if key in self._inflight or len(self._inflight) >= 4:
                return {"ai.discovery.status": "busy"}
            self._inflight.add(key)
        try:
            try:
                result = await asyncio.wait_for(_lookup(request, credential, scope), timeout=_TIMEOUT)
            except _LookupError as error:
                result = {"ai.discovery.status": str(error)}
            except (asyncio.TimeoutError, httpx.TimeoutException):
                result = {"ai.discovery.status": "timeout"}
            except Exception:
                # Never log exception text, URLs, headers, credential hints or bodies.
                result = {"ai.discovery.status": "unavailable"}
            with self._lock:
                self._cache[key] = (time.monotonic() + (300 if result.get("ai.route.api_key_id") else 60), result)
                self._cache.move_to_end(key)
                while len(self._cache) > 256:
                    self._cache.popitem(last=False)
            return dict(result)
        finally:
            with self._lock:
                self._inflight.discard(key)
