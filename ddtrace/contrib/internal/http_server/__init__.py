"""
Patch the standard library ``http.server`` module (``BaseHTTPRequestHandler``).

This integration does **not** create spans. Its only purpose is detecting the AWS Lambda
MicroVM ``/run`` lifecycle hook (see :ref:`AWS Lambda MicroVM identity refresh
<aws-lambda-microvm-identity-refresh>`) for applications that implement that hook with a raw
``http.server`` handler instead of a supported web framework.


Enabling
~~~~~~~~

The http_server integration is disabled by default. Enable it with
``DD_TRACE_HTTP_SERVER_ENABLED=true`` alongside :ref:`ddtrace-run<ddtracerun>` or
:ref:`import ddtrace.auto<ddtraceauto>`::

    DD_TRACE_HTTP_SERVER_ENABLED=true ddtrace-run ....
"""
