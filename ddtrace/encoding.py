import msgpack

from .compat import json


def encode_json(traces):
    """
    Encodes a list of traces, expecting a list of items where each items
    is a list of spans. Before dump the string in a JSON format, the list
    is flatten.

    :param traces: A list of traces that should be serialized
    """
    spans = flatten_spans(traces)
    return json.dumps(spans)


def encode_msgpack(traces):
    """
    Encodes a list of traces, expecting a list of items where each items
    is a list of spans. Before encoding using MessagePack binary format, the
    list is flatten.

    :param traces: A list of traces that should be serialized
    """
    spans = flatten_spans(traces)
    return msgpack.packb(spans, use_bin_type=True)


def flatten_spans(traces):
    """
    TODO
    """
    return [span.to_dict() for trace in traces for span in trace]


def encode_services(services):
    """
    Encodes a dictionary of services.

    :param services: A dictionary that contains one or more services
    """
    return json.dumps(services)
