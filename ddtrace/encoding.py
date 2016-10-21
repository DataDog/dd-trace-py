from .compat import json


def encode_spans(traces):
    """
    Encodes a list of traces. It expects a list of items where each items
    is a list of spans.

    :param traces: A list of traces that should be serialized
    """
    spans = [span.to_dict() for trace in traces for span in trace]
    return json.dumps(spans)

def encode_services(services):
    """
    Encodes a dictionary of services.

    :param services: A dictionary that contains one or more services
    """
    return json.dumps(services)
