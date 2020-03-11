import json
import struct
import copy

import msgpack

from .internal.logger import get_logger


log = get_logger(__name__)


class _EncoderBase(object):
    """
    Encoder interface that provides the logic to encode traces and service.
    """
    def encode_traces(self, traces):
        """
        Encodes a list of traces, expecting a list of items where each items
        is a list of spans. Before dump the string in a serialized format all
        traces are normalized, calling the ``to_dict()`` method. The traces
        nesting is not changed.

        :param traces: A list of traces that should be serialized
        """
        normalized_traces = [[span.to_dict() for span in trace] for trace in traces]
        return self.encode(normalized_traces)

    def encode_trace(self, trace):
        """
        Encodes a trace, expecting a list of spans. Before dump the string in a
        serialized format all traces are normalized, calling the ``to_dict()`` method.
        The traces nesting is not changed.

        :param trace: A list of traces that should be serialized
        """
        return self.encode([span.to_dict() for span in trace])

    @staticmethod
    def encode(obj):
        """
        Defines the underlying format used during traces or services encoding.
        This method must be implemented and should only be used by the internal functions.
        """
        raise NotImplementedError

    @staticmethod
    def decode(data):
        """
        Defines the underlying format used during traces or services encoding.
        This method must be implemented and should only be used by the internal functions.
        """
        raise NotImplementedError

    @staticmethod
    def join_encoded(objs):
        """Helper used to join a list of encoded objects into an encoded list of objects"""
        raise NotImplementedError


class JSONEncoder(_EncoderBase):
    content_type = 'application/json'

    @staticmethod
    def encode(obj):
        return json.dumps(obj)

    @staticmethod
    def decode(data):
        return json.loads(data)

    @staticmethod
    def join_encoded(objs):
        """Join a list of encoded objects together as a json array"""
        return '[' + ','.join(objs) + ']'


class JSONEncoderV2(JSONEncoder):
    """
    JSONEncoderV2 encodes traces to the new intake API format.
    The main differences are all ids are 
    """

    content_type = "application/json"

    @staticmethod
    def encode(obj):
        # Copy, so we can modify the ids to be strings without
        # changing to original object

        for trace in obj:
            for span in trace:
                span["trace_id"] = JSONEncoderV2._encode_id_to_hex(span["trace_id"])
                span["parent_id"] = JSONEncoderV2._encode_id_to_hex(span["parent_id"])
                span["span_id"] = JSONEncoderV2._encode_id_to_hex(span["span_id"])

        return json.dumps(obj)

    @staticmethod
    def decode(data):
        data = json.loads(data)
        for trace in data:
            for span in trace:
                span["trace_id"] = JSONEncoderV2._decode_id_to_hex(span["trace_id"])
                span["parent_id"] = JSONEncoderV2._decode_id_to_hex(span["parent_id"])
                span["span_id"] = JSONEncoderV2._decode_id_to_hex(span["span_id"])
        return data

    @staticmethod
    def join_encoded(objs):
        """Join a list of encoded objects together as a json array"""
        return '{"traces":[' + ",".join(objs) + "]}"

    @staticmethod
    def _encode_id_to_hex(id):
        return "%0.16X" % int(id)

    @staticmethod
    def _decode_id_to_hex(id):
        return int(id, 16)


class MsgpackEncoder(_EncoderBase):
    content_type = 'application/msgpack'

    @staticmethod
    def encode(obj):
        return msgpack.packb(obj)

    @staticmethod
    def decode(data):
        if msgpack.version[:2] < (0, 6):
            return msgpack.unpackb(data)
        return msgpack.unpackb(data, raw=True)

    @staticmethod
    def join_encoded(objs):
        """Join a list of encoded objects together as a msgpack array"""
        buf = b''.join(objs)

        # Prepend array header to buffer
        # https://github.com/msgpack/msgpack-python/blob/f46523b1af7ff2d408da8500ea36a4f9f2abe915/msgpack/fallback.py#L948-L955
        count = len(objs)
        if count <= 0xf:
            return struct.pack('B', 0x90 + count) + buf
        elif count <= 0xffff:
            return struct.pack('>BH', 0xdc, count) + buf
        else:
            return struct.pack('>BI', 0xdd, count) + buf


Encoder = MsgpackEncoder
