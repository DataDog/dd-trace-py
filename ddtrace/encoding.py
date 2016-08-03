"""
Serialization code.
"""


from .compat import json


def encode_spans(spans):
    return json.dumps([s.to_dict() for s in spans])

def encode_services(services):
    return json.dumps(services)
