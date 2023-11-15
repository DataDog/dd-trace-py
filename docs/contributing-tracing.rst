The Tracing Product
===================

The Tracing product code in ``ddtrace`` generates and sends ``Span`` instances to the Datadog backend. To do
this, it inspects the ``ExecutionContext`` tree built by integration code.

How should the Tracer select service name?
------------------------------------------
Service Naming and Peer.Service help with automated service detection and modeling
by providing different default values for the service and operation name span
attributes.  These attributes are adjusted by using environment variables which toggle
various schema. Every integration is required to support the existing schema.

The API to support service name, peer.service, and schema can be found here: <https://github.com/DataDog/dd-trace-py/blob/1.x/ddtrace/internal/schema/__init__.py#L52-L64>

The important elements are:
1. Every integration needs a default service name, which is what the service name for spans will be when the integration is called.
2. The default service name should be wrapped with `schematize_service_name()`
3. If the span being created is a supported OpenTelemetry format:

  1. Wrap your operation name with the appropriate `schematize_*_operation` call (or add a new one)
  2. If OpenTelemetry specifies precursors for peer.service, ensure your span includes those as tags
