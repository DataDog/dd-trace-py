import os
from typing import Dict

from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import tracer


log = get_logger(__name__)

# Configure the playwright integration
config._add(
    "playwright",
    {
        "distributed_tracing": asbool(os.getenv("DD_PLAYWRIGHT_DISTRIBUTED_TRACING", default=True)),
    },
)


def get_version() -> str:
    """Get the Playwright version."""
    try:
        import playwright

        return getattr(playwright, "__version__", "")
    except (ImportError, AttributeError):
        return ""


def _supported_versions() -> Dict[str, str]:
    return {"playwright": "*"}


def _get_tracing_headers() -> Dict[str, str]:
    """
    Get distributed tracing headers for the current span context.

    Returns a dictionary with headers like:
      - x-datadog-trace-id
      - x-datadog-parent-id
      - x-datadog-sampling-priority (114 for test contexts)
      - x-datadog-tags (optional)
    """
    if not config.playwright.get("distributed_tracing", True):
        return {}

    headers = {}
    try:
        test_span = tracer.current_span()
        if not test_span:
            raise Exception("Failed to get test span")

        # HTTPPropagator.inject mutates the headers dict in place
        test_span.context._meta["_dd.p.test_id"] = str(test_span.span_id)
        test_span.context._meta["_dd.p.test_session_id"] = test_span.get_tag("test_session_id")
        test_span.context._meta["_dd.p.test_suite_id"] = test_span.get_tag("test_suite_id")
        HTTPPropagator.inject(test_span.context, headers)

    except Exception as e:
        log.debug("Failed to get distributed tracing headers: %s", e)

    return headers


def _patch_browser_context_new_context():
    """Patch Browser.new_context to inject distributed tracing headers."""
    try:
        from playwright.sync_api import Browser
    except ImportError:
        log.debug("Playwright not available for patching")
        return

    original_new_context = Browser.new_context

    def _wrapped_new_context(*args, **kwargs):
        # Get distributed tracing headers for current context
        dd_headers = _get_tracing_headers()

        # Add headers to extra_http_headers (for navigation requests)
        if dd_headers:
            extra_headers = kwargs.setdefault("extra_http_headers", {})
            extra_headers.update(dd_headers)

        # Create the browser context
        context = original_new_context(*args, **kwargs)

        # Store headers on context for route handler to reuse
        if dd_headers:
            context._dd_tracing_headers = dd_headers
            _install_route_handler(context)

        return context

    Browser.new_context = _wrapped_new_context


def _install_route_handler(context) -> None:
    """
    Install route handler to inject headers into JavaScript-initiated requests.

    JavaScript fetch() and XHR requests don't inherit extra_http_headers,
    so we intercept them via route handler and inject headers manually.
    """
    if hasattr(context, "_dd_route_handler_installed"):
        return

    try:

        def _inject_headers_handler(route, request):
            """Inject distributed tracing headers into the request."""
            try:
                # Get request headers and merge in our tracing headers
                headers = dict(getattr(request, "headers", {}) or {})
                headers.update(getattr(context, "_dd_tracing_headers", {}))

                # Continue request with merged headers
                route.continue_(headers=headers)

            except Exception as e:
                # Fallback: continue without modification if injection fails
                log.debug("Failed to inject headers in route handler: %s", e)
                try:
                    route.continue_()
                except Exception as continue_error:
                    log.debug("Failed to continue route after header injection failure: %s", continue_error)

        # Install catch-all route handler
        context.route("**/*", _inject_headers_handler)
        context._dd_route_handler_installed = True

    except Exception as e:
        log.debug("Failed to install route handler: %s", e)


def _patch_api_request_new_context():
    """Patch playwright.request.new_context for API requests."""
    try:
        import playwright

        if not hasattr(playwright, "request"):
            return

        original_new_context = playwright.request.new_context

        def _wrapped_api_new_context(*args, **kwargs):
            # Get and inject distributed tracing headers
            dd_headers = _get_tracing_headers()
            if dd_headers:
                extra_headers = kwargs.setdefault("extra_http_headers", {})
                extra_headers.update(dd_headers)

            return original_new_context(*args, **kwargs)

        playwright.request.new_context = _wrapped_api_new_context

    except Exception as e:
        log.debug("Failed to patch API request context: %s", e)


def patch() -> None:
    """Apply the Playwright integration patch."""
    try:
        import playwright
    except ImportError:
        log.debug("Playwright not available, skipping patch")
        return

    if getattr(playwright, "_datadog_patch", False):
        return

    try:
        # Patch Browser.new_context for browser contexts
        _patch_browser_context_new_context()

        # Patch API request context
        _patch_api_request_new_context()

        playwright._datadog_patch = True
        log.debug("Playwright integration patched successfully")

    except Exception as e:
        log.debug("Failed to patch Playwright: %s", e)


def unpatch() -> None:
    """Remove the Playwright integration patch."""
    try:
        import playwright
    except ImportError:
        return

    if not getattr(playwright, "_datadog_patch", False):
        return

    try:
        from playwright.sync_api import Browser

        # Restore original methods if they were patched
        if hasattr(Browser, "_original_new_context"):
            Browser.new_context = Browser._original_new_context
            delattr(Browser, "_original_new_context")

        # Restore API request context
        if hasattr(playwright, "request") and hasattr(playwright.request, "_original_new_context"):
            playwright.request.new_context = playwright.request._original_new_context
            delattr(playwright.request, "_original_new_context")

        playwright._datadog_patch = False
        log.debug("Playwright integration unpatched successfully")

    except Exception as e:
        log.debug("Failed to unpatch Playwright: %s", e)
