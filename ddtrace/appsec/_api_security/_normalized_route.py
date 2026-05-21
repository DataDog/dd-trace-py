"""RFC-1103 normalized HTTP route computation.

Computes the value of the ``_dd.appsec.normalized_route`` span tag from a
framework-specific route declaration. Each web integration uses its own URL
grammar, so this module exposes one normalizer per supported framework:

- ``normalize_route`` — Starlette / FastAPI ``{name}`` / ``{name:converter}``
  grammar. Mount prefixes are already prepended by the integration.
- ``normalize_route_django`` — Django ``<name>`` / ``<converter:name>``
  ``path()`` grammar, plus ``re_path()`` regexes with ``(?P<name>...)`` named
  groups. ``include()`` mounts are pre-joined by Django itself.

All normalizers take the assembled route string and the matched
``path_params`` mapping for the current request, and return the per-request
normalized route, or ``None`` when the input is malformed (in which case the
caller must omit the tag rather than emit an inaccurate value).

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

from collections.abc import Set as AbstractSet
from functools import lru_cache
import re
from typing import Any
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Union


# Atoms emitted by ``_parse_django_segment`` are 2-tuples ``(_KIND_STATIC|_KIND_PARAM|_KIND_CATCHALL, value)`` and
# 3-tuples ``(_KIND_PARAM, "paramN", args_index)`` for top-level unnamed captures (the trailing element is the
# positional slot the framework's resolver wrote the value into). ``Any`` keeps the alias compatible with both arities.
_Atom = tuple[Any, ...]
_ParsedRoute = tuple[tuple[tuple[_Atom, ...], ...], bool]
# ``path_params`` is polymorphic: dict for named-group captures, sequence for all-unnamed captures, None otherwise.
_PathParams = Optional[Union[Mapping[str, Any], Sequence[Any]]]


# Mirror of starlette.routing.PARAM_REGEX. Inlined to avoid importing starlette
# from the appsec module — this code is reachable from any web integration's
# set_http_meta dispatch, so we must not assume starlette is installed.
_PARAM_REGEX = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(:[a-zA-Z_][a-zA-Z0-9_]*)?\}")

# Fast-path detector. Matches when each URL segment is either entirely static
# with only RFC rule 3 safe characters, or exactly one ``{name}`` /
# ``{name:type}`` parameter (excluding the ``:path`` catch-all) with no
# surrounding static text. In that case the only required transformation is
# stripping converter suffixes — see _STRIP_CONVERTOR.
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
            tail = "{" + _encode_param_name(catch_all.group(1)) + "}"
            result = "/" + "/".join(out_segments + [tail]) if out_segments else "/" + tail
            if keep_trailing:
                result += "/"
            return result

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


def normalize_route(route: Optional[str], path_params: _PathParams = None) -> Optional[str]:
    """Return the RFC-1103 ``_dd.appsec.normalized_route`` for a Starlette/FastAPI route.

    ``path_params`` is accepted for API parity with frameworks that support optional path elements. Starlette and
    FastAPI don't, so it is currently unused. Returning ``None`` signals the caller to omit the tag.
    """
    del path_params
    if not route or not isinstance(route, str) or not route.startswith("/"):
        return None
    return _normalize_route_cached(route)


# Django re_path() named-group prefix. Body parens are balanced below to find the closing ``)``. Django ``path()``
# converters (``<name>`` / ``<converter:name>``) are detected by a leading ``<``; ``path:`` is the only multi-segment
# catch-all in Django's default registry, so custom multi-segment converters are out of scope.
_DJANGO_NAMED_GROUP_PREFIX = "(?P<"

# Pre-scan patterns reserving ``paramK`` (K a positive integer) when used as a framework-supplied parameter name, so
# auto-generated placeholders for unnamed groups never collide (RFC-1103 rule 4). The converter form only matters for
# include()-joined routes mixing a ``path()`` parent with an unnamed ``re_path()`` child — rare, but the scan is cheap.
# Combined pre-scan: both ``(?P<paramK>`` (regex named group) and ``<paramK>`` / ``<converter:paramK>`` (path()
# converter) in one alternation, so the route is walked only once.
_DJANGO_PARAM_N_RESERVED = re.compile(r"(?:\(\?P<param|<(?:[a-zA-Z_][a-zA-Z0-9_]*:)?param)(\d+)>")
_EMPTY_INT_SET: frozenset[int] = frozenset()

# Integer kinds for emitted atoms. Compared against ``atom[0]`` on every request, so a small int compare beats string
# equality (even with CPython interning the comparison is one extra check).
_KIND_STATIC = 0
_KIND_PARAM = 1
_KIND_CATCHALL = 2

# Fast-path detector for ``path()``-only Django routes. Matches when each segment is either entirely static with safe
# characters from RFC rule 3, or exactly one ``<name>`` / ``<converter:name>`` parameter (excluding ``path:`` catch-all
# which has non-trivial trailing-element semantics). The route must not start with ``/`` (Django routes never do).
# In this shape the output is independent of ``path_params`` — all captures are required ``path()`` converters — so
# the cached fast-path result is reusable across requests.
_DJANGO_FAST_PATH_REGEX = re.compile(
    r"^(?!/)"
    r"(?:[A-Za-z0-9.\-~_]+|<(?:(?!path:)[a-zA-Z_][a-zA-Z0-9_]*:)?[a-zA-Z_][a-zA-Z0-9_]*>)?"
    r"(?:/(?:[A-Za-z0-9.\-~_]+|<(?:(?!path:)[a-zA-Z_][a-zA-Z0-9_]*:)?[a-zA-Z_][a-zA-Z0-9_]*>))*"
    r"/?$"
)
_DJANGO_STRIP_CONVERTER = re.compile(r"<(?:[a-zA-Z_][a-zA-Z0-9_]*:)?([a-zA-Z_][a-zA-Z0-9_]*)>")


@lru_cache(maxsize=256)
def _normalize_route_django_fast_path(route: str) -> Optional[str]:
    """Return the normalized route via the fast path, or ``None`` if not eligible.

    Eligibility: the whole route matches ``_DJANGO_FAST_PATH_REGEX`` — all segments are pure-static-safe or one
    ``path()`` converter, no ``path:`` catch-all, no regex anchors or extensions. In that shape the result doesn't
    depend on ``path_params`` (all captures are required) so we can produce the final string straight from the route.
    """
    if not route:
        # Empty route matches the fast-path regex (every component is optional) but yields a meaningless ``"/"``.
        # Public ``normalize_route_django`` rejects empty strings earlier; guard here too so the helper is safe to
        # call directly from tests or future internal call sites.
        return None
    if not _DJANGO_FAST_PATH_REGEX.match(route):
        return None
    return "/" + _DJANGO_STRIP_CONVERTER.sub(r"{\1}", route)


def _balance_group_body(segment: str, start: int, capture_idx_in: int) -> tuple[int, int, int]:
    """Walk from ``start`` (just after the opening ``(``) until depth returns to 0.

    Counts nested captures so the caller can map subsequent emitted atoms to the right ``args[i]`` slot from Django's
    resolver. Only constructs allocating a positional group bump the counter: plain ``(`` and ``(?P<name>...)``. All
    other ``(?...)`` syntaxes (``(?:``, lookarounds, comments, inline flags, ``(?P=name)``, conditionals) are balanced
    through transparently — matching Python ``re``'s group numbering rules.

    Returns ``(end_idx, depth, new_capture_idx)``. ``depth != 0`` means the body was unbalanced (e.g. a group spanning
    a slash) — the caller treats this as malformed and omits the tag.
    """
    depth = 1
    j = start
    n = len(segment)
    capture_idx = capture_idx_in
    while j < n and depth > 0:
        cc = segment[j]
        if cc == "\\" and j + 1 < n:
            # Escape: skip both chars (e.g. ``\(`` ``\)`` ``\[`` ``\\``).
            j += 2
            continue
        if cc == "[":
            # Character class: ``(``/``)`` inside it are literal; ``\]`` is too.
            j += 1
            while j < n and segment[j] != "]":
                if segment[j] == "\\" and j + 1 < n:
                    j += 2
                else:
                    j += 1
            j += 1
            continue
        if cc == "(":
            depth += 1
            # ``(`` allocates a group slot iff it is not followed by ``?`` (plain unnamed capture) or it begins the
            # ``(?P<name>...)`` named-capture form. All other ``(?...)`` extensions don't allocate.
            next_is_question = j + 1 < n and segment[j + 1] == "?"
            if not next_is_question or segment.startswith(_DJANGO_NAMED_GROUP_PREFIX, j):
                capture_idx += 1
            j += 1
            continue
        if cc == ")":
            depth -= 1
        j += 1
    return j, depth, capture_idx


def _parse_django_segment(
    segment: str,
    is_regex: bool,
    capture_idx_start: int,
    used_paramN: AbstractSet[int],
    next_paramN_start: int,
) -> Optional[tuple[list[tuple[Any, ...]], int, int]]:
    """Tokenize one URL segment (no slashes) into atoms.

    Returns ``(atoms, new_capture_idx, new_next_paramN)`` on success, or ``None`` when the segment can't be safely
    interpreted (per RFC-1103: omit the tag rather than emit a guess).

    Atom kinds:

    - ``(_KIND_STATIC, literal)`` — a run of coalesced literal characters (URL-encoded per rule 3 at assembly time).
    - ``(_KIND_PARAM, name)`` — a named single-segment param; per-request filter looks ``name`` up in ``path_params``.
    - ``(_KIND_PARAM, "paramN", args_index)`` — a top-level unnamed capture. ``args_index`` is the slot Python ``re``
      wrote this group's value into (Django forwards as ``resolver_match.args``). Nested captures inside are counted
      (to keep subsequent atoms aligned with ``args``) but not emitted — per RFC-1103 the outer group is the
      user-visible param.
    - ``(_KIND_CATCHALL, name)`` — Django's ``<path:name>`` converter; one tail atomic element per rule 5.

    Group handling: ``(?P<name>...)`` → named param (body may contain anything); ``(...)`` → placeholder ``paramN``
    (``N`` skipping any ``paramK`` already used as a named group in the route); ``(?:...)`` and other top-level
    ``(?...)`` extensions are rejected — alternation has no clean RFC mapping and plain-literal wrappers are rare
    enough not to warrant special-casing. (Inside a group body the same extensions are balanced through transparently.)

    ``is_regex`` is True for ``re_path()``-shaped routes (caller-detected from the original string). On regex routes,
    top-level ``|*+?`` outside any group are rejected — alternation or quantifiers on a preceding literal that we can't
    safely interpret. On ``path()`` routes the same characters are valid path literals and become URL-encoded statics.
    """
    atoms: list[tuple[Any, ...]] = []
    i = 0
    n = len(segment)
    capture_idx = capture_idx_start
    next_paramN = next_paramN_start

    def _assign_paramN() -> int:
        nonlocal next_paramN
        # Skip integers already taken by a framework-supplied ``paramK`` so placeholder names stay unique (rule 4).
        while next_paramN in used_paramN:
            next_paramN += 1
        result = next_paramN
        next_paramN += 1
        return result

    # Buffer consecutive static chars so a run of N literal characters produces one ``(_KIND_STATIC, "literal")`` atom
    # instead of N single-char atoms — fewer tuple allocations at parse time and a single fast string concat per
    # segment at assembly time.
    static_buf: list[str] = []

    def _flush_static() -> None:
        if static_buf:
            atoms.append((_KIND_STATIC, "".join(static_buf)))
            static_buf.clear()

    while i < n:
        c = segment[i]
        if c == "<":
            end = segment.find(">", i + 1)
            if end == -1:
                return None
            inner = segment[i + 1 : end]
            if ":" in inner:
                converter, _, name = inner.partition(":")
            else:
                converter, name = "str", inner
            if not name.isidentifier() or not converter.isidentifier():
                return None
            _flush_static()
            atoms.append((_KIND_CATCHALL if converter == "path" else _KIND_PARAM, name))
            i = end + 1
        elif segment.startswith(_DJANGO_NAMED_GROUP_PREFIX, i):
            name_end = segment.find(">", i + 4)
            if name_end == -1:
                return None
            name = segment[i + 4 : name_end]
            if not name.isidentifier():
                return None
            # This open paren is itself a capturing group; count it before walking the body so nested counts follow.
            capture_idx += 1
            j, depth, capture_idx = _balance_group_body(segment, name_end + 1, capture_idx)
            if depth != 0:
                return None
            _flush_static()
            atoms.append((_KIND_PARAM, name))
            i = j
            # Optional ``?`` / lazy ``??`` quantifier makes the group optional; per-request assembly drops it when
            # ``path_params`` shows it didn't match.
            if i < n and segment[i] == "?":
                i += 1
                if i < n and segment[i] == "?":
                    i += 1
        elif c == "(" and i + 1 < n and segment[i + 1] == "?":
            # ``(?...)`` extension at the segment top level — ``(?:`` non-capt, lookarounds, comments, inline flags,
            # ``(?P=name)`` backref, conditionals. None have a clean RFC-1103 mapping at this level (no positional
            # capture, no user-visible parameter), so refuse rather than guess. ``(?P<name>...)`` is handled above.
            return None
        elif c == "(":
            # Top-level unnamed capture. Record its ``args`` slot before walking the body so the lookup at filter time
            # hits the right tuple position even when the body has nested captures we silently count past.
            this_args_idx = capture_idx
            capture_idx += 1
            j, depth, capture_idx = _balance_group_body(segment, i + 1, capture_idx)
            if depth != 0:
                return None
            num = _assign_paramN()
            _flush_static()
            atoms.append((_KIND_PARAM, "param{}".format(num), this_args_idx))
            i = j
            # Optional ``?`` / lazy ``??`` quantifier makes the whole capture optional — filtered via ``args[idx]``.
            if i < n and segment[i] == "?":
                i += 1
                if i < n and segment[i] == "?":
                    i += 1
        elif c == "[":
            # Top-level character class — ambiguous to normalize.
            return None
        elif is_regex and c in "|*+?{":
            # Top-level regex meta on a re_path-shaped route: alternation, a quantifier on the preceding static char,
            # or a ``{n,m}`` repetition quantifier — none representable in RFC-1103 syntax without guessing.
            return None
        elif c == "\\" and i + 1 < n:
            static_buf.append(segment[i + 1])
            i += 2
        else:
            static_buf.append(c)
            i += 1
    _flush_static()
    # Catch-all must be the last atom in its segment. A static prefix is allowed (rule 5: "the entire tail ... is one
    # atomic element") but a static suffix is structurally impossible — ``<path:...>`` consumes to end-of-URL.
    for idx, atom in enumerate(atoms):
        if atom[0] == _KIND_CATCHALL and idx != len(atoms) - 1:
            return None
    return atoms, capture_idx, next_paramN


@lru_cache(maxsize=256)
def _parse_django_route(route: str) -> Optional[_ParsedRoute]:
    """Parse a Django route into ``(segments_atoms, keep_trailing)``.

    Returned tuples are immutable so the result is shareable across requests under ``lru_cache``. ``None`` means the
    route grammar is unsupported and the tag must be omitted — same omit-rather-than-guess contract as the per-request
    assembly. All structural validation happens here (anchors, ``/?``, consecutive slashes, catch-all-must-be-terminal);
    the per-request assembly only filters atoms by ``path_params`` and re-applies RFC rule 5.
    """
    # Detect ``re_path()``-shaped routes from the *original* string before stripping anchors below. Leading ``^``,
    # trailing ``$``, or a ``(?P<...>`` anywhere are strong signals — all three survive ``include()`` joining (the
    # parent's ``^`` is stripped by Django but the child's anchors and named groups are not). On regex routes,
    # ``_parse_django_segment`` rejects top-level ``|*+?``; on path() routes those characters are valid literals.
    is_regex = route.startswith("^") or route.endswith("$") or _DJANGO_NAMED_GROUP_PREFIX in route

    body = route
    # Regex anchors are not part of the URL grammar; strip them before splitting.
    if body.startswith("^"):
        body = body[1:]
    if body.endswith("$"):
        body = body[:-1]
    # ``/?`` declares an optional trailing slash. Per RFC-1103 rule 1 that is not "declared with a trailing slash".
    if body.endswith("/?"):
        body = body[:-2]
    # Django routes never start with a slash — the leading slash is implicit. Strip if present (e.g. ``^/`` regex
    # anchor) so the segment split is consistent.
    if body.startswith("/"):
        body = body[1:]

    keep_trailing = len(body) > 0 and body.endswith("/")
    if keep_trailing:
        body = body[:-1]

    if not body:
        return (), keep_trailing

    # Pre-scan: reserve any ``paramK`` already supplied as a parameter name via either ``(?P<paramK>`` (regex named
    # group) or ``<paramK>`` / ``<converter:paramK>`` (path() converter), so auto-generated placeholders for unnamed
    # captures never collide (RFC-1103 rule 4). Short-circuit on the cheap substring check so routes without ``param``
    # in any form skip the scan entirely.
    if "param" in route:
        used_paramN: AbstractSet[int] = {int(m.group(1)) for m in _DJANGO_PARAM_N_RESERVED.finditer(route)}
    else:
        used_paramN = _EMPTY_INT_SET

    segments = body.split("/")
    parsed: list[tuple[_Atom, ...]] = []
    last_idx = len(segments) - 1
    # ``re``'s group numbering is global to the regex, so ``capture_idx`` (the index into Django's ``args`` tuple) and
    # ``next_paramN`` (the placeholder counter) both thread across segments.
    capture_idx = 0
    next_paramN = 1
    for i, segment in enumerate(segments):
        if not segment:
            # Rule 2: empty atomic elements (consecutive slashes) are illegal.
            return None
        result = _parse_django_segment(segment, is_regex, capture_idx, used_paramN, next_paramN)
        if result is None:
            return None
        atoms, capture_idx, next_paramN = result
        # Catch-all (``<path:...>``) must be the final segment — rule 5.
        if any(a[0] == _KIND_CATCHALL for a in atoms) and i != last_idx:
            return None
        parsed.append(tuple(atoms))
    return tuple(parsed), keep_trailing


# ``mode`` constants for ``_param_present`` — computed once per call from the shape of ``path_params`` rather than
# isinstance-checking per atom. Cheap int compares replace the per-atom polymorphic dispatch.
_MODE_NO_INFO = 0  # ``path_params is None`` → assume every atom present
_MODE_MAPPING = 1  # named-group atoms looked up by name; unnamed atoms have no signal
_MODE_SEQUENCE = 2  # unnamed atoms looked up by ``args_index``; named atoms have no signal


def _classify_path_params(path_params: Any) -> int:
    # Defensive: declared as ``Any`` rather than ``_PathParams`` because the listener boundary accepts arbitrary input
    # (a misbehaving plugin could pass anything). The branches below classify into the three modes ``_param_present``
    # knows how to handle; anything that doesn't match falls through to "no info" rather than risk a ``TypeError`` or
    # a wrong-shaped lookup. ``Sequence[Any]`` technically includes ``str`` / ``bytes`` so they're excluded explicitly
    # — indexing into one of those would yield a single character / byte rather than a meaningful capture.
    if path_params is None:
        return _MODE_NO_INFO
    if isinstance(path_params, Mapping):
        return _MODE_MAPPING
    if isinstance(path_params, (str, bytes, bytearray)):
        return _MODE_NO_INFO
    if isinstance(path_params, Sequence):
        return _MODE_SEQUENCE
    return _MODE_NO_INFO


def _param_present(atom: _Atom, path_params: _PathParams, mode: int) -> bool:
    r"""Return True when the atom was bound to a non-empty value in this request.

    ``mode`` is the pre-classified shape of ``path_params`` (see ``_classify_path_params``). Hoisting it out of this
    function lets the caller compute the isinstance check once per request rather than once per atom — measurable on
    routes with several parameters. A value of ``None`` or ``""`` counts as absent: matches Python regex semantics for
    unmatched optional groups and zero-width captures. Cross cases (named atom vs. sequence ``path_params``; unnamed
    atom vs. mapping ``path_params``) are "no signal" — Django's mixed routes drop unnamed values from both ``args``
    and ``kwargs``, so we have no way to verify presence and must assume present.
    """
    if mode == _MODE_NO_INFO:
        return True
    has_args_idx = len(atom) > 2
    if mode == _MODE_MAPPING:
        if has_args_idx:
            return True
        name = atom[1]
        if name not in path_params:  # type: ignore[operator]
            return False
        value = path_params[name]  # type: ignore[index]
    else:
        if not has_args_idx:
            return True
        args_idx = atom[2]
        if args_idx >= len(path_params):  # type: ignore[arg-type]
            return False
        value = path_params[args_idx]  # type: ignore[index]
    return value is not None and value != ""


def normalize_route_django(route: Optional[str], path_params: _PathParams = None) -> Optional[str]:
    """Return the RFC-1103 ``_dd.appsec.normalized_route`` for a Django route.

    Accepts ``path()`` declarations (``<converter:name>``) and ``re_path()`` regexes (``(?P<name>...)``, ``(...)``).
    ``include()`` mounts arrive pre-joined in ``resolver_match.route`` so no prefix handling is needed.

    When ``path_params`` is provided, any declared parameter whose value resolved to ``None`` or ``""`` — e.g. an
    optional ``re_path`` group that didn't match — is dropped from the normalized route. This implements RFC-1103
    rule 6's per-request resolution of optional path elements: the same declaration may emit several distinct
    normalized routes across requests. Required parameters always have non-empty bindings so they're never affected.

    ``path_params`` is polymorphic: a ``Mapping`` (Django's ``resolver_match.kwargs``) for routes with any named group,
    or a positional sequence (``resolver_match.args``) for routes whose captures are all unnamed. See
    ``_param_present`` for the lookup rules. Returning ``None`` signals the caller to omit the tag.
    """
    if not route or not isinstance(route, str):
        return None
    # Fast path: ``path()``-only routes (no regex, no catch-all, no optionals) have a path_params-independent output
    # we can cache as a final string. This mirrors the Starlette/FastAPI fast path in spirit.
    fast = _normalize_route_django_fast_path(route)
    if fast is not None:
        return fast
    parsed = _parse_django_route(route)
    if parsed is None:
        return None
    segments_atoms, keep_trailing = parsed

    # Classify ``path_params`` once so per-atom calls into ``_param_present`` skip the isinstance dispatch.
    mode = _classify_path_params(path_params)

    out_segments: list[str] = []
    for atoms in segments_atoms:
        # Single pass over the segment's atoms: collect statics, filter params by presence, and watch for a catch-all
        # — replacing four separate comprehensions (each iterating the whole list) with one walk.
        catch_all: Optional[_Atom] = None
        kept_params: list[_Atom] = []
        kept_statics: list[str] = []
        had_params = False
        for a in atoms:
            kind = a[0]
            if kind == _KIND_PARAM:
                had_params = True
                if _param_present(a, path_params, mode):
                    kept_params.append(a)
            elif kind == _KIND_STATIC:
                kept_statics.append(a[1])
            else:  # _KIND_CATCHALL
                catch_all = a

        # Catch-all wins the segment: the entire tail (any in-segment static prefix plus the gobbled remainder) is one
        # atomic element.
        if catch_all is not None:
            if not _param_present(catch_all, path_params, mode):
                # Catch-all didn't bind anything (rare; possible with custom converters) — drop the trailing segment.
                continue
            tail = "{" + _encode_param_name(catch_all[1]) + "}"
            result = "/" + "/".join(out_segments + [tail]) if out_segments else "/" + tail
            if keep_trailing:
                result += "/"
            return result

        if had_params and not kept_params:
            # All dynamic content for this segment dropped (e.g. a sole ``(?P<id>\d+)?`` that didn't match). The
            # remaining static glue (e.g. dots in Rails-style ``:a.:b``) would be misleading on its own — drop the
            # whole segment since the URL didn't have a corresponding piece.
            continue

        if not kept_params:
            text = "".join(kept_statics)
            if text:
                out_segments.append(_encode_static(text))
        elif len(kept_params) == 1:
            out_segments.append("{" + _encode_param_name(kept_params[0][1]) + "}")
        else:
            combined = "+".join(_encode_param_name(p[1]) for p in kept_params)
            out_segments.append("{" + combined + "}")

    if not out_segments:
        return "/"
    result = "/" + "/".join(out_segments)
    if keep_trailing:
        result += "/"
    return result
