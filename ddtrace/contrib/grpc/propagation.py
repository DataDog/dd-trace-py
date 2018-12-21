import grpc
import collections


class ClientCallDetails(
        collections.namedtuple(
            '_ClientCallDetails',
            ('method', 'timeout', 'metadata', 'credentials')),
        grpc.ClientCallDetails):
    """Copy/paste from https://github.com/grpc/grpc/blob/d0cb61eada9d270b9043ec866b55c88617d362be/examples/python/interceptors/headers/header_manipulator_client_interceptor.py#L22
    """  # noqa
    pass


def inject_span(span, client_call_details):
    """Inject propagation headers in grpc call metadata.
    Recreates a new object
    """
    metadata = []
    if client_call_details.metadata is not None:
        metadata = list(client_call_details.metadata)
    metadata.append((b'x-datadog-trace-id', str(span.trace_id)))
    metadata.append((b'x-datadog-parent-id', str(span.span_id)))

    if (span.context.sampling_priority) is not None:
        metadata.append((b'x-datadog-sampling-priority', str(span.context.sampling_priority)))
    client_call_details = ClientCallDetails(
        client_call_details.method, client_call_details.timeout, metadata,
        client_call_details.credentials)
    return client_call_details
