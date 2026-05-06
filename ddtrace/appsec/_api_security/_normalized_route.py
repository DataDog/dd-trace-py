"""RFC-1103 normalized HTTP route for Starlette / FastAPI.

Computes the value of the ``_dd.appsec.normalized_route`` span tag from a
Starlette-style route declaration (the same path grammar used by both Starlette
``Route`` and FastAPI ``APIRoute``: ``{name}`` or ``{name:convertor}``).

The function is integration-agnostic: it takes the assembled route string
(mount prefixes already prepended by the integration) and the matched
``path_params`` dict, and returns the per-request normalized route, or
``None`` when the input is malformed (in which case the caller must omit the
tag rather than emit an inaccurate value).

Designed to add as little per-request overhead as possible:

- Results are memoized by route string. Routes are defined statically at app
  startup, so after the first request through each route, the function is
  effectively a dict lookup.
- A regex-based fast path handles the common shape (each URL segment is either
  fully static with safe characters, or exactly one ``{name}`` / ``{name:type}``
  parameter, no ``:path`` catch-all). It produces the normalized route via a
  single regex match + single substitution, with no string splits or list
  allocations.
- The slow path covers the harder cases (URL-encoding, multi-param-in-segment,
  catch-all tail, illegal inputs) and is structurally identical to the original
  reference implementation.
"""

from functools import lru_cache
import re
from typing import Any
from typing import Mapping
from typing import Optional


# Mirror of starlette.routing.PARAM_REGEX. Inlined to avoid importing starlette
# from the appsec module — this code is reachable from any web integration's
# set_http_meta dispatch, so we must not assume starlette is installed.
_PARAM_REGEX = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(:[a-zA-Z_][a-zA-Z0-9_]*)?\}")

# Fast-path detector. Matches when each URL segment is either entirely static
# with only RFC rule 3 safe characters, or exactly one ``{name}`` /
# ``{name:type}`` parameter (excluding the ``:path`` catch-all) with no
# surrounding static text. In that case the only required transformation is
# stripping convertor suffixes — see _STRIP_CONVERTOR.
_FAST_PATH_REGEX = re.compile(
    r"^(?:/(?:[A-Za-z0-9.\-~_]+|\{[a-zA-Z_][a-zA-Z0-9_]*(?::(?!path\})[a-zA-Z_][a-zA-Z0-9_]*)?\}))*/?$"
)
_STRIP_CONVERTOR = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*):[a-zA-Z_][a-zA-Z0-9_]*\}")

# RFC rule 3: static-constant safe set is [A-Za-z0-9.-~_].
_STATIC_SAFE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-~_")

# RFC rule 4: parameter names may contain anything except these 6 characters.
_PARAM_NAME_RESERVED = frozenset("/?#+{}")


def _percent_encode(ch: str) -> str:
    return "".join("%{:02X}".format(b) for b in ch.encode("utf-8"))


def _encode_static(segment: str) -> str:
    return "".join(c if c in _STATIC_SAFE else _percent_encode(c) for c in segment)


def _encode_param_name(name: str) -> str:
    # Only the 6 reserved characters get percent-encoded — see RFC rule 4.
    # For Starlette/FastAPI this is effectively dead code (PARAM_REGEX restricts
    # names to [a-zA-Z0-9_]), but we keep it for spec compliance.
    if not _PARAM_NAME_RESERVED.intersection(name):
        return name
    return "".join(_percent_encode(c) if c in _PARAM_NAME_RESERVED else c for c in name)


def _normalize_route_slow(route: str) -> Optional[str]:
    keep_trailing = len(route) > 1 and route.endswith("/")
    body = route[1:-1] if keep_trailing else route[1:]
    if not body:
        return "/"

    segments = body.split("/")
    out_segments = []

    for i, segment in enumerate(segments):
        if not segment:
            # Rule 2: empty atomic elements (consecutive slashes) are illegal.
            return None

        matches = list(_PARAM_REGEX.finditer(segment))
        if not matches:
            out_segments.append(_encode_static(segment))
            continue

        catch_all = next((m for m in matches if m.group(2) == ":path"), None)
        if catch_all is not None:
            # Rule 5 catch-all exception: the entire tail (any in-segment
            # static prefix plus the gobbled remainder) is one atomic element.
            if i != len(segments) - 1:
                return None
            return (
                "/" + "/".join(out_segments + ["{" + _encode_param_name(catch_all.group(1)) + "}"])
                if out_segments
                else "/{" + _encode_param_name(catch_all.group(1)) + "}"
            )

        if len(matches) == 1:
            out_segments.append("{" + _encode_param_name(matches[0].group(1)) + "}")
        else:
            combined = "+".join(_encode_param_name(m.group(1)) for m in matches)
            out_segments.append("{" + combined + "}")

    result = "/" + "/".join(out_segments)
    if keep_trailing:
        result += "/"
    return result


@lru_cache(maxsize=256)
def _normalize_route_cached(route: str) -> Optional[str]:
    if _FAST_PATH_REGEX.match(route):
        return _STRIP_CONVERTOR.sub(r"{\1}", route)
    return _normalize_route_slow(route)


def normalize_route(route: Optional[str], path_params: Optional[Mapping[str, Any]] = None) -> Optional[str]:
    """Return the RFC-1103 ``_dd.appsec.normalized_route`` for a Starlette/FastAPI route.

    ``path_params`` is accepted for API parity with frameworks that support
    optional path elements. Starlette and FastAPI don't, so it is currently
    unused. Returning ``None`` signals the caller to omit the tag.
    """
    del path_params
    if not route or not isinstance(route, str) or not route.startswith("/"):
        return None
    return _normalize_route_cached(route)
