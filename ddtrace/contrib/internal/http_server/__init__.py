"""
Patch the standard library ``http.server`` module (``BaseHTTPRequestHandler``).

This integration does **not** create spans. Its only purpose is detecting the AWS Lambda
MicroVM ``/run`` lifecycle hook for applications that implement that hook with a raw
``http.server`` handler instead of a supported web framework.


Enabling
~~~~~~~~

The http_server integration is enabled by default only in AWS Lambda MicroVM
environments. Use :ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`
to enable it there, or set ``DD_TRACE_HTTP_SERVER_ENABLED=true`` to force-enable it
elsewhere. Disable it with ``DD_TRACE_HTTP_SERVER_ENABLED=false`` if needed::

    DD_TRACE_HTTP_SERVER_ENABLED=false ddtrace-run ....
"""
