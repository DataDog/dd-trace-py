
"""
AWS X-Ray Propagator
--------------------

The **AWS X-Ray Propagator** provides a propagator that when used, adds a `trace
header` to outgoing traces that is compatible with the AWS X-Ray backend
service. This allows the trace context to be propagated when a trace spans
multiple AWS services.

The same propagator setup is used to extract a context sent by external systems
so that child span have the correct parent context.

**NOTE**: Because the parent context parsed from the ``X-Amzn-Trace-Id`` header
assumes the context is _not_ sampled by default, users should make sure to add
``Sampled=1`` to their ``X-Amzn-Trace-Id`` headers so that the child spans are
sampled.

Usage
-----

Use the provided AWS X-Ray Propagator to inject the necessary context into
traces sent to external systems.

This can be done by either setting this environment variable:

::

    



API
---
.. _trace header: https://docs.aws.amazon.com/xray/latest/devguide/xray-concepts.html#xray-concepts-tracingheader
"""

import logging
import typing
from os import environ

from ddtrace import tracer
from ddtrace.context import Context


TRACE_HEADER_KEY = "X-Amzn-Trace-Id"
AWS_TRACE_HEADER_ENV_KEY = "_X_AMZN_TRACE_ID"
KV_PAIR_DELIMITER = ";"
KEY_AND_VALUE_DELIMITER = "="

TRACE_ID_KEY = "Root"
TRACE_ID_LENGTH = 35
TRACE_ID_VERSION = "1"
TRACE_ID_DELIMITER = "-"
TRACE_ID_DELIMITER_INDEX_1 = 1
TRACE_ID_DELIMITER_INDEX_2 = 10
TRACE_ID_FIRST_PART_LENGTH = 8

PARENT_ID_KEY = "Parent"
PARENT_ID_LENGTH = 16

SAMPLED_FLAG_KEY = "Sampled"
SAMPLED_FLAG_LENGTH = 1
IS_SAMPLED = "1"
NOT_SAMPLED = "0"


_logger = logging.getLogger(__name__)


class AwsParseTraceHeaderError(Exception):
    def __init__(self, message):
        super().__init__()
        self.message = message


class _AwsXRayPropagator:
    """Propagator for the AWS X-Ray Trace Header propagation protocol.

    See:
    https://docs.aws.amazon.com/xray/latest/devguide/xray-concepts.html#xray-concepts-tracingheader
    """

    @staticmethod
    def _extract(carrier):
        trace_header = carrier.get(TRACE_HEADER_KEY)

        if not trace_header:
            return None

        try:
            trace_id, span_id, sampling_flag = _AwsXRayPropagator._extract_span_properties(trace_header)
        except AwsParseTraceHeaderError as err:
            _logger.debug(err.message)
            return None

        context = Context(
            trace_id=trace_id,
            span_id=span_id,
            sampling_priority=sampling_flag,
        )

        return context

    @staticmethod
    def _inject(span_context: Context, carrier) -> None:
        if not span_context:
            return

        dd_trace_id = f"{span_context.trace_id:032x}"
        xray_trace_id = TRACE_ID_DELIMITER.join(
            [
                TRACE_ID_VERSION,
                dd_trace_id[:TRACE_ID_FIRST_PART_LENGTH],
                dd_trace_id[TRACE_ID_FIRST_PART_LENGTH:],
            ]
        )

        parent_id = f"{span_context.span_id:016x}"

        trace_header = KV_PAIR_DELIMITER.join(
            [
                KEY_AND_VALUE_DELIMITER.join([key, value])
                for key, value in [
                    (TRACE_ID_KEY, xray_trace_id),
                    (PARENT_ID_KEY, parent_id),
                    (SAMPLED_FLAG_KEY, str(int(span_context.sampling_priority))),
                ]
            ]
        )

        carrier[TRACE_HEADER_KEY] = trace_header

    @staticmethod
    def _extract_span_properties(trace_header):
        trace_id = None
        span_id = None
        sampling_flag = None

        for kv_pair_str in trace_header.split(KV_PAIR_DELIMITER):
            try:
                key_str, value_str = kv_pair_str.split(KEY_AND_VALUE_DELIMITER)
                key, value = key_str.strip(), value_str.strip()
            except ValueError as ex:
                raise AwsParseTraceHeaderError(
                    f"Error parsing X-Ray trace header. Invalid key value pair: {kv_pair_str}. Returning INVALID span context."
                ) from ex
            if key == TRACE_ID_KEY:
                if not _AwsXRayPropagator._validate_trace_id(value):
                    raise AwsParseTraceHeaderError(
                        f"Invalid TraceId in X-Ray trace header: '{TRACE_HEADER_KEY}' with value '{trace_header}'. Returning INVALID span context."
                    )

                try:
                    trace_id = _AwsXRayPropagator._parse_trace_id(value)
                except ValueError as ex:
                    raise AwsParseTraceHeaderError(
                        f"Invalid TraceId in X-Ray trace header: '{TRACE_HEADER_KEY}' with value '{trace_header}'. Returning INVALID span context."
                    ) from ex
            elif key == PARENT_ID_KEY:
                if not _AwsXRayPropagator._validate_span_id(value):
                    raise AwsParseTraceHeaderError(
                        f"Invalid ParentId in X-Ray trace header: '{TRACE_HEADER_KEY}' with value '{trace_header}'. Returning INVALID span context."
                    )

                try:
                    span_id = _AwsXRayPropagator._parse_span_id(value)
                except ValueError as ex:
                    raise AwsParseTraceHeaderError(
                        f"Invalid TraceId in X-Ray trace header: '{TRACE_HEADER_KEY}' with value '{trace_header}'. Returning INVALID span context."
                    ) from ex
            elif key == SAMPLED_FLAG_KEY:
                if not _AwsXRayPropagator._validate_sampled_flag(value):
                    raise AwsParseTraceHeaderError(
                        f"Invalid Sampling flag in X-Ray trace header: '{TRACE_HEADER_KEY}' with value '{trace_header}'. Returning INVALID span context."
                    )

                sampling_flag = _AwsXRayPropagator._parse_sampled_flag(value)

        return trace_id, span_id, sampling_flag

    @staticmethod
    def _validate_trace_id(trace_id_str):
        return (
            len(trace_id_str) == TRACE_ID_LENGTH
            and trace_id_str.startswith(TRACE_ID_VERSION)
            and trace_id_str[TRACE_ID_DELIMITER_INDEX_1] == TRACE_ID_DELIMITER
            and trace_id_str[TRACE_ID_DELIMITER_INDEX_2] == TRACE_ID_DELIMITER
        )

    @staticmethod
    def _parse_trace_id(trace_id_str):
        timestamp_subset = trace_id_str[TRACE_ID_DELIMITER_INDEX_1 + 1 : TRACE_ID_DELIMITER_INDEX_2]
        unique_id_subset = trace_id_str[TRACE_ID_DELIMITER_INDEX_2 + 1 : TRACE_ID_LENGTH]
        return int(timestamp_subset + unique_id_subset, 16)

    @staticmethod
    def _validate_span_id(span_id_str):
        return len(span_id_str) == PARENT_ID_LENGTH

    @staticmethod
    def _parse_span_id(span_id_str):
        return int(span_id_str, 16)

    @staticmethod
    def _validate_sampled_flag(sampled_flag_str):
        return len(sampled_flag_str) == SAMPLED_FLAG_LENGTH and sampled_flag_str in (IS_SAMPLED, NOT_SAMPLED)

    @staticmethod
    def _parse_sampled_flag(sampled_flag_str):
        return float(sampled_flag_str[0])


class AwsXrayLambdaPropagator(_AwsXRayPropagator):
    """Implementation of the AWS X-Ray Trace Header propagation protocol but
    with special handling for Lambda's ``_X_AMZN_TRACE_ID` environment
    variable.
    """

    def _extract(carrier) -> Context:
        xray_context = super()._extract(carrier)

        if xray_context and xray_context.trace_id:
            return xray_context

        trace_header = environ.get(AWS_TRACE_HEADER_ENV_KEY)

        if trace_header is None:
            return xray_context

        return super()._extract({TRACE_HEADER_KEY: trace_header})