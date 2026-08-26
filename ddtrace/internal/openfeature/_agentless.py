"""
Helpers for the agentless Feature Flagging configuration source.

These are pure, dependency-light functions (endpoint derivation, JSON:API
validation, gzip decoding) used by the agentless poller. They deliberately take
their inputs explicitly rather than reading global config so they can be unit
tested in isolation.
"""

import gzip
import json
from typing import Any
from typing import Optional
from urllib.parse import urlencode
from urllib.parse import urlsplit
from urllib.parse import urlunsplit


# Canonical rules-based server path appended to the managed CDN host and to
# custom base URLs that only supply an origin.
DEFAULT_AGENTLESS_PATH = "/api/v2/feature-flagging/config/rules-based/server"


def build_agentless_endpoint(site: str, env: Optional[str] = None, base_url: Optional[str] = None) -> str:
    """Build the agentless UFC endpoint URL.

    Without a custom ``base_url`` the managed Datadog CDN endpoint is derived
    from ``site`` (lowercased), adding ``dd_env`` only when ``env`` is set. This
    resolves staging (``datad0g.com``) and GovCloud (``ddog-gov.com``)
    automatically with no site allowlist.

    A custom ``base_url`` that is a root/origin receives the standard rules-based
    path; one with a non-root path is used verbatim as the exact endpoint. HTTP
    is permitted for custom endpoints (operator-owned trust).

    :raises ValueError: if a custom ``base_url`` is malformed or uses a scheme
        other than http/https. Error messages never include the URL, which is
        sensitive.
    """
    configured = base_url.strip() if base_url else ""

    if not configured:
        netloc = "ufc-server.ff-cdn.{}".format(site.strip().lower())
        query = urlencode({"dd_env": env}) if env else ""
        return urlunsplit(("https", netloc, DEFAULT_AGENTLESS_PATH, query, ""))

    # A URL with internal whitespace is malformed; urlsplit is lenient and would
    # otherwise accept it. Do not surface the value.
    if any(ch.isspace() for ch in configured):
        raise ValueError("Invalid Feature Flagging agentless URL")

    try:
        parts = urlsplit(configured)
    except ValueError:
        raise ValueError("Invalid Feature Flagging agentless URL")

    if parts.scheme not in ("http", "https"):
        raise ValueError("Feature Flagging agentless URL must use HTTP or HTTPS")
    if not parts.netloc:
        raise ValueError("Invalid Feature Flagging agentless URL")

    path = parts.path
    if path in ("", "/"):
        path = DEFAULT_AGENTLESS_PATH

    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def decode_response_body(body: bytes, content_encoding: Optional[str]) -> bytes:
    """Return ``body`` decompressed when the response was gzip-encoded.

    gzip is NOT auto-decoded by the HTTP layer, so callers pass the raw body and
    the ``Content-Encoding`` header value. The check is case-insensitive.

    :raises: propagates gzip errors (e.g. ``OSError``/``EOFError``) on a body
        that claims gzip encoding but cannot be decompressed.
    """
    if content_encoding and content_encoding.strip().lower() == "gzip":
        return gzip.decompress(body)
    return body


def parse_ufc_configuration(body: Any) -> "dict[str, Any]":
    """Validate a JSON:API UFC response envelope and return ``data.attributes``.

    The envelope must be::

        {"data": {"type": "universal-flag-configuration",
                  "attributes": {"format": <str>, "createdAt": <str>,
                                 "environment": {"name": <str>}, "flags": {}}}}

    Only ``data.attributes`` is returned (and passed to the evaluator). Raw UFC
    (non-JSON:API) is not accepted, even for custom endpoints.

    :raises ValueError: if the payload is not valid JSON or does not match the
        JSON:API Universal Flag Configuration v1 contract.
    """
    try:
        payload = json.loads(body)
    except (ValueError, TypeError) as e:
        raise ValueError("Malformed UFC payload") from e

    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON:API Universal Flag Configuration resource")

    data = payload.get("data")
    if not isinstance(data, dict) or data.get("type") != "universal-flag-configuration":
        raise ValueError("Expected a JSON:API Universal Flag Configuration resource")

    attributes = data.get("attributes")
    if not isinstance(attributes, dict):
        raise ValueError("Expected a Universal Flag Configuration v1 object")

    environment = attributes.get("environment")
    if (
        not isinstance(attributes.get("format"), str)
        or not isinstance(attributes.get("createdAt"), str)
        or not isinstance(environment, dict)
        or not isinstance(environment.get("name"), str)
        or not isinstance(attributes.get("flags"), dict)
    ):
        raise ValueError("Expected a Universal Flag Configuration v1 object")

    return attributes
