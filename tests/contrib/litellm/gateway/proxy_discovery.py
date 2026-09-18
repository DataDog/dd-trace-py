"""TEST ONLY: keep the real proxy callback, but substitute local provider inventory."""

from ddtrace.contrib.internal.litellm import _gateway_discovery as discovery
from tests.contrib.litellm.gateway.proxy_auth import authenticate  # noqa: F401


_capture = discovery.ProviderKeyDiscovery.capture


def capture(self, kwargs, route, headers=None):
    # Only our local mock inference endpoint stands in for the native provider.
    provider = route.get("ai.route.provider")
    native = dict(route)
    if route.get("ai.route.endpoint_host") == "127.0.0.1" and provider in ("anthropic", "openai"):
        native["ai.route.endpoint_host"] = f"api.{provider}.com"
    result = _capture(self, kwargs, native, headers)
    if "ai.discovery.status" in native:
        route["ai.discovery.status"] = native["ai.discovery.status"]
    return result


async def inventory(self, path, **query):
    assert "SYNTHETIC-ADMIN" in str(self.headers)
    if path == "/v1/organizations/me":
        return {"id": "org-anthropic-response"}
    provider = "anthropic" if self.host == "api.anthropic.com" else "openai"
    if path.endswith("/api_keys"):
        return {
            "data": [
                {
                    "id": f"key_discovered_{provider}",
                    "partial_key_hint": "sk-SYNTHETIC...SECRET",
                    "redacted_value": "sk-SYNTHETIC...SECRET",
                }
            ],
            "has_more": False,
        }
    raise AssertionError("Unexpected inventory path")


discovery.ProviderKeyDiscovery.capture = capture
discovery._Inventory.get = inventory
