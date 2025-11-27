import os
from typing import Dict

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanTypes
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


def _inject_distributed_tracing_headers(headers: Dict[str, str], context=None, is_test_context=None) -> None:
    """
    Inject Datadog distributed tracing headers into the provided headers dict.

    This uses the provided context (if any), otherwise falls back to the current active span context.
    If no active span exists, creates a temporary span for header injection.
    """
    if not config.playwright.get("distributed_tracing", True):
        return

    try:
        # Use provided context, or get the current active span
        span_context = context

        # Use the explicitly provided is_test_context flag, or try to detect it
        if is_test_context is None and span_context is not None:
            # Check if the provided context is from a test span by walking up the span hierarchy
            # This is needed because the context object itself doesn't store the test.type tag
            try:
                current_span = tracer.current_span()
                while current_span:
                    if current_span.context == span_context and current_span.get_tag('test.type') == 'test':
                        is_test_context = True
                        break
                    current_span = current_span._parent
            except Exception:
                pass
        elif span_context is None:
            current_span = tracer.current_span()
            if current_span:
                span_context = current_span.context

        if span_context:
            # Use the span context to inject headers, passing the test context flag
            HTTPPropagator.inject(span_context, headers, is_test_context=is_test_context)
        else:
            # No active span, create a temporary span for header injection
            with tracer.trace("playwright.browser.request", span_type=SpanTypes.HTTP) as span:
                span._set_tag_str(SPAN_KIND, "client")
                span._set_tag_str("component", config.playwright.integration_name)
                HTTPPropagator.inject(span.context, headers, is_test_context=is_test_context)

    except Exception as e:
        log.debug("Failed to inject distributed tracing headers: %s", e)


def _patch_browser_context_new_context():
    """Patch Browser.new_context to inject headers at the context level."""
    try:
        from playwright.sync_api import Browser
    except ImportError:
        log.debug("Playwright not available for patching")
        return

    original_new_context = Browser.new_context

    def _wrapped_new_context(*args, **kwargs):
        # Capture the current span context at the time of context creation
        # This ensures test context is preserved for async browser requests
        current_span = tracer.current_span()
        test_context = current_span.context if current_span else None

        # Inject headers into extra_http_headers
        headers = kwargs.setdefault("extra_http_headers", {})
        is_test_context_flag = current_span and current_span.get_tag('test.type') == 'test'
        _inject_distributed_tracing_headers(headers, test_context, is_test_context_flag)

        # Create the context
        context = original_new_context(*args, **kwargs)

        # Store the test context and test flag on the context for use by route handlers
        context._dd_test_context = test_context
        context._dd_is_test_context = current_span and current_span.get_tag('test.type') == 'test'

        # Also install a route handler as a fallback
        _install_route_handler(context)

        return context

    Browser.new_context = _wrapped_new_context


def _patch_api_request_new_context():
    """Patch playwright.request.new_context for API requests."""
    try:
        import playwright

        if not hasattr(playwright, "request"):
            return

        original_new_context = playwright.request.new_context

        def _wrapped_api_new_context(*args, **kwargs):
            # Inject headers into extra_http_headers for API requests
            headers = kwargs.setdefault("extra_http_headers", {})
            _inject_distributed_tracing_headers(headers)

            return original_new_context(*args, **kwargs)

        playwright.request.new_context = _wrapped_api_new_context

    except Exception as e:
        log.debug("Failed to patch API request context: %s", e)


def _install_route_handler(context) -> None:
    """
    Install a catch-all route handler on the context to inject headers into all requests.

    This ensures headers are injected even if the context was created without
    extra_http_headers or if individual requests override headers.
    """
    if not hasattr(context, "_dd_route_handler_installed"):
        try:

            def _inject_headers(route, request):
                """Route handler that injects distributed tracing headers into each request."""
                try:
                    # Get existing headers
                    headers = dict(getattr(request, "headers", {}) or {})

                    # Use the stored test context and flag from when the browser context was created
                    test_context = getattr(context, "_dd_test_context", None)
                    is_test_context = getattr(context, "_dd_is_test_context", None)

                    # Inject our distributed tracing headers
                    _inject_distributed_tracing_headers(headers, test_context, is_test_context)

                    # Continue the request with injected headers
                    route.continue_(headers=headers)

                except Exception as e:
                    # Fallback: continue without headers if injection fails
                    log.debug("Failed to inject headers in route handler: %s", e)
                    try:
                        route.continue_()
                    except Exception:
                        pass

            # Install catch-all route handler
            context.route("**/*", _inject_headers)
            context._dd_route_handler_installed = True

        except Exception as e:
            log.debug("Failed to install route handler: %s", e)


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
