from collections import deque
from functools import lru_cache

from tornado.routing import PathMatches
from tornado.web import HTTPError

from ddtrace import config
from ddtrace.contrib.internal import trace_utils
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal._exceptions import find_exception
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.trace import tracer

from .constants import CONFIG_KEY
from .constants import REQUEST_SPAN_KEY
from .stack_context import TracerStackContext


async def execute(func, handler, args, kwargs):
    """
    Wrap the handler execute method so that the entire request is within the same
    ``TracerStackContext``. This simplifies users code when the automatic ``Context``
    retrieval is used via ``Tracer.trace()`` method.
    """
    # retrieve tracing settings
    settings = handler.settings[CONFIG_KEY]
    service = settings["default_service"]
    distributed_tracing = settings["distributed_tracing"]

    with TracerStackContext():
        with core.context_with_data(
            "tornado.request",
            span_name=schematize_url_operation("tornado.request", protocol="http", direction=SpanDirection.INBOUND),
            span_type=SpanTypes.WEB,
            service=service,
            tags={},
            distributed_headers=handler.request.headers,
            integration_config=config.tornado,
            activate_distributed_headers=True,
            distributed_headers_config_override=distributed_tracing,
            headers_case_sensitive=True,
        ) as ctx:
            req_span = ctx.span
            request = handler.request
            method = getattr(request, "method", "")
            protocol = getattr(request, "protocol", "http")
            host = getattr(request, "host", "")
            uri = getattr(request, "uri", "")
            full_url = f"{protocol}://{host}{uri}"
            query_string = getattr(request, "query", "")
            query_parameters = (
                request.arguments if hasattr(request, "arguments") else getattr(request, "query_arguments", {})
            )
            headers = {k.lower(): v for k, v in getattr(request, "headers", {}).items()}
            cookies = {k: handler.get_cookie(k) for k in handler.cookies.keys()}

            headers.pop("cookie", None)  # Remove Cookie from headers to avoid duplication

            ctx.set_item("req_span", req_span)
            core.dispatch("web.request.start", (ctx, config.tornado))

            http_route, path_params = _find_route(handler.application.default_router.rules, handler.request)
            if http_route is not None and isinstance(http_route, str):
                req_span._set_attribute("http.route", http_route)
            setattr(request, REQUEST_SPAN_KEY, req_span)
            trace_utils.set_http_meta(
                req_span,
                config.tornado,
                method=method,
                url=full_url,
                query=query_string,
                request_headers=headers,
                raw_uri=full_url,
                parsed_query=query_parameters,
                peer_ip=request.remote_ip,
                route=http_route,
                headers_are_case_sensitive=True,
                request_cookies=cookies,
                request_path_params=path_params,
            )
            dispatch_res = core.dispatch_with_results("tornado.start_request", ("tornado", handler)).tornado_future
            if dispatch_res and dispatch_res.value is not None:
                return await dispatch_res.value
            try:
                return await func(*args, **kwargs)
            except BlockingException as b:
                dispatch_res = core.dispatch_with_results(
                    "tornado.block_request", ("tornado", handler, b.args[0])
                ).tornado_future
                if dispatch_res and dispatch_res.value is not None:
                    return await dispatch_res.value
            except BaseException as exc:
                # managing python 3.11+ BaseExceptionGroup with compatible code for 3.10 and below
                if blocking_exc := find_exception(exc, BlockingException):
                    dispatch_res = core.dispatch_with_results(
                        "tornado.block_request", ("tornado", handler, blocking_exc.args[0])
                    ).tornado_future
                    if dispatch_res and dispatch_res.value is not None:
                        return await dispatch_res.value
                else:
                    raise


# Regex group prefixes that mark a group as non-capturing (kept verbatim in the route).
# Full Python re syntax for groups starting with '(':
#   (?:...)   non-capturing group
#   (?=...)   positive lookahead
#   (?!...)   negative lookahead
#   (?<=...)  positive lookbehind
#   (?<!...)  negative lookbehind
#   (?>...)   atomic group
#   (?(...)   conditional group
#   (?P=name) named backreference  — NOT a capturing group despite starting with ?P
#
# Capturing groups — intentionally absent, they become %s in the route:
#   (...)         positional capturing group
#   (?P<name>...) named capturing group
#
# Comments — handled separately in _regex_to_route, removed entirely from the route:
#   (?#...)  inline comment — matches nothing, contributes nothing to the path
_NON_CAPTURING_PREFIXES = ("?:", "?=", "?!", "?<=", "?<!", "?>", "?(", "?P=")


@lru_cache(maxsize=512)
def _regex_to_route(pattern: str) -> str:
    """
    Convert a compiled regex pattern to an ``http.route``-style string.

    This mirrors what Tornado's ``PathMatches._find_groups`` produces: capturing
    groups (positional ``(...)`` and named ``(?P<name>...)``) are replaced with
    ``%s``, while non-capturing constructs (``(?:...)``, lookaheads, lookbehinds)
    are kept verbatim so the route remains readable.

    Unlike ``_find_groups`` this never returns ``None`` — it always produces a
    usable route string regardless of pattern complexity.
    """
    # Strip the anchors that Tornado's PathMatches.__init__ appends.
    if pattern.startswith("^"):
        pattern = pattern[1:]
    if pattern.endswith("$"):
        pattern = pattern[:-1]

    result = []
    i = 0
    n = len(pattern)

    while i < n:
        if pattern[i] == "\\":
            # Escaped character — copy both chars and move on.
            result.append(pattern[i : i + 2])
            i += 2
            continue

        if pattern[i] == "[":
            # Character class — copy verbatim until the closing ']'.
            # Inside '[…]', '(' is literal, not a group boundary.
            j = i + 1
            if j < n and pattern[j] == "^":
                j += 1
            # A ']' immediately after '[' or '[^' is a literal ']', not the end.
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                if pattern[j] == "\\":
                    j += 2
                else:
                    j += 1
            if j < n:
                j += 1  # skip the closing ']'
            result.append(pattern[i:j])
            i = j
            continue

        if pattern[i] != "(":
            result.append(pattern[i])
            i += 1
            continue

        # Peek past the opening parenthesis to decide whether this is capturing.
        suffix = pattern[i + 1 :]
        capturing = not any(suffix.startswith(p) for p in _NON_CAPTURING_PREFIXES)

        # Find the matching closing paren, respecting nesting and escapes.
        depth = 1
        j = i + 1
        while j < n and depth > 0:
            if pattern[j] == "\\":
                j += 2
            elif pattern[j] == "(":
                depth += 1
                j += 1
            elif pattern[j] == ")":
                depth -= 1
                j += 1
            else:
                j += 1

        if suffix.startswith("?#"):
            # Inline comment (?#...) — matches nothing, omit from route entirely.
            pass
        else:
            result.append("%s" if capturing else pattern[i:j])
        i = j

    return "".join(result)


def _path_for_path_match(matcher: PathMatches) -> str:
    if matcher._path is not None:
        return matcher._path

    return _regex_to_route(matcher.regex.pattern)


def _find_route(initial_rule_set, request):
    """
    We have to walk through the same chain of rules that tornado does to find a matching rule.
    """
    rules = deque()

    for rule in initial_rule_set:
        rules.append(rule)

    while len(rules) > 0:
        rule = rules.popleft()
        matcher = rule.matcher
        if (m := matcher.match(request)) is not None:
            if isinstance(matcher, PathMatches):
                path_args = m.get("path_args", []) or m.get("path_kwargs", {})
                return _path_for_path_match(matcher), path_args

            elif hasattr(rule.target, "rules"):
                rules.extendleft(reversed(rule.target.rules))

    return "^$", {}


def _on_flush(func, handler, args, kwargs):
    """
    Wrap the ``RequestHandler.flush`` method.
    """
    # safe-guard: expected arguments -> set_status(self, status_code, reason=None)
    core.dispatch("tornado.send_response", ("tornado", handler))
    return func(*args, **kwargs)


def on_finish(func, handler, args, kwargs):
    """
    Wrap the ``RequestHandler.on_finish`` method. This is the last executed method
    after the response has been sent, and it's used to retrieve and close the
    current request span (if available).
    """
    request = handler.request
    request_span = getattr(request, REQUEST_SPAN_KEY, None)
    if request_span:
        # use the class name as a resource; if an handler is not available, the
        # default handler class will be used so we don't pollute the resource
        # space here
        klass = handler.__class__
        request_span.resource = "{}.{}".format(klass.__module__, klass.__name__)
        core.dispatch(
            "web.request.finish",
            (
                request_span,
                config.tornado,
                request.method,
                request.full_url().rsplit("?", 1)[0],
                handler.get_status(),
                request.query,
                None,
                None,
                None,
                True,
            ),
        )
    return func(*args, **kwargs)


def log_exception(func, handler, args, kwargs):
    """
    Wrap the ``RequestHandler.log_exception``. This method is called when an
    Exception is not handled in the user code. In this case, we save the exception
    in the current active span. If the Tornado ``Finish`` exception is raised, this wrapper
    will not be called because ``Finish`` is not an exception.
    """
    # safe-guard: expected arguments -> log_exception(self, typ, value, tb)
    try:
        value = get_argument_value(args, kwargs, 1, "value")
    except ArgumentError:
        value = None

    if not value:
        return func(*args, **kwargs)

    # retrieve the current span
    current_span = tracer.current_span()

    if not current_span:
        return func(*args, **kwargs)

    if isinstance(value, HTTPError):
        # Tornado uses HTTPError exceptions to stop and return a status code that
        # is not a 2xx. In this case we want to check the status code to be sure that
        # only 5xx are traced as errors, while any other HTTPError exception is handled as
        # usual.
        if config._http_server.is_error_code(value.status_code):
            current_span.set_exc_info(*args)
    else:
        # any other uncaught exception should be reported as error
        current_span.set_exc_info(*args)

    return func(*args, **kwargs)
