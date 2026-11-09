"""WAF input semantics independent of native storage and allocation."""

from collections.abc import Mapping
from collections.abc import Sequence
import struct

from ddtrace.internal.native._native import ddwaf


def reference_encode(value, max_objects=256, max_depth=20, max_string_length=4096):
    truncation = [None, None, None]

    def record(index, size):
        truncation[index] = max(truncation[index] or 0, size)

    def text(data):
        if len(data) > max_string_length:
            record(0, len(data))
        return ("string", data[:max_string_length])

    def visit(obj, depth):
        if type(obj) is str:
            return text(obj.encode("utf-8", "ignore"))
        if type(obj) is bytes:
            return text(obj)
        if type(obj) is bool:
            return ("bool", obj)
        if type(obj) is int:
            return ("signed", (obj + 2**63) % 2**64 - 2**63)
        if type(obj) is float:
            return ("float", struct.unpack("<Q", struct.pack("<d", obj))[0])
        if obj is None:
            return ("null",)
        if isinstance(obj, (Mapping, Sequence)):
            cap = min(max_objects, 65535) if depth > 0 else 0
            if depth <= 0:
                record(2, 20)
            output = []
            mapping = isinstance(obj, Mapping)
            items = obj.items() if mapping else enumerate(obj)
            for key, child in items:
                if mapping and type(key) not in (str, bytes):
                    continue
                if len(output) == cap:
                    record(1, len(obj))
                    break
                child = visit(child, depth - 1)
                output.append((visit(key, depth), child) if mapping else child)
            return ("map" if mapping else "array", output)
        return text(str(obj).encode("utf-8", "ignore"))

    return visit(value, max_depth), tuple(truncation)


def encoded_value(value, **limits):
    view, truncation = ddwaf._encode(value, **limits)

    def decode(obj):
        kind, *parts = obj
        if kind == "string":
            return parts[0].decode("utf-8", "ignore")
        if kind == "array":
            return [decode(item) for item in parts[0]]
        if kind == "map":
            return {decode(key): decode(item) for key, item in parts[0]}
        if kind == "float":
            return struct.unpack("<d", struct.pack("<Q", parts[0]))[0]
        return parts[0] if parts else None

    return decode(view), truncation
