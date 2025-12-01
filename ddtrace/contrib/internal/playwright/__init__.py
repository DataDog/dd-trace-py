"""
Trace the Playwright browser automation library to trace browser requests and enable distributed tracing.

Enabling
~~~~~~~~

The Playwright integration is **automatically enabled** when using pytest with the ``--ddtrace`` flag
if the ``pytest-playwright`` plugin is detected. No manual patching is required.

To disable automatic instrumentation, set the environment variable::

    DD_TRACE_PLAYWRIGHT_ENABLED=false

For manual patching (outside of pytest contexts), use :func:`patch()<ddtrace.patch>`::

    from ddtrace import patch
    patch(playwright=True)

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.playwright['distributed_tracing']

   Include distributed tracing headers in browser requests sent from Playwright.
   This option can also be set with the ``DD_PLAYWRIGHT_DISTRIBUTED_TRACING``
   environment variable.

   Default: ``True``

Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

The integration can be configured per instance::

    from ddtrace import config

    # Disable distributed tracing globally.
    config.playwright['distributed_tracing'] = False

Headers tracing is supported for this integration.

How It Works
~~~~~~~~~~~~

The Playwright integration automatically injects Datadog distributed tracing headers
into all browser requests made through Playwright. This enables end-to-end tracing
from your application through to browser-initiated backend requests.

The integration uses a dual approach to ensure headers are injected:
1. **Context-level injection**: Headers added to BrowserContext.extra_http_headers (navigation)
2. **Route interception**: A catch-all route handler for JavaScript-initiated requests (fetch, XHR)

Headers injected include:
- ``x-datadog-trace-id``: The lower 64-bits of the 128-bit trace-id in decimal format
- ``x-datadog-parent-id``: The 64-bits span-id of the current span in decimal format
- ``x-datadog-sampling-priority``: Sampling decision (optional)
- ``x-datadog-origin``: Origin information (optional, not used for browser requests)
- ``x-datadog-tags``: Supplemental trace state information (optional)

This integration is particularly useful for E2E testing scenarios where you want to
trace requests from browser automation through to your backend services.
"""
