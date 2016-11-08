from .compat import json


def encode_traces(traces):
    """
    Encodes a list of traces, expecting a list of items where each items
    is a list of spans. Before dump the string in a JSON format, the list
    is flatten.

    :param traces: A list of traces that should be serialized
    """
    flatten_spans = [span.to_dict() for trace in traces for span in trace]
    return json.dumps(flatten_spans)

def encode_services(services):
    """
    Encodes a dictionary of services.

    :param services: A dictionary that contains one or more services
    """
    return json.dumps(services)
