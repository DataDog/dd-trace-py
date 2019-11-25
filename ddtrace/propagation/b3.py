# Copyright 2019, OpenTelemetry Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from ..context import Context
from ..ext import priority


class B3HTTPPropagator:
    """b3 compatible propagator"""

    SINGLE_HEADER_KEY = 'b3'
    TRACE_ID_KEY = 'x-b3-traceid'
    SPAN_ID_KEY = 'x-b3-spanid'
    SAMPLED_KEY = 'x-b3-sampled'
    FLAGS_KEY = 'x-b3-flags'
    _SAMPLE_PROPAGATE_VALUES = set(['1', 'True', 'true', 'd'])

    _SAMPLING_PRIORITY_MAP = {
        priority.USER_REJECT: '0',
        priority.AUTO_REJECT: '0',
        priority.AUTO_KEEP: '1',
        priority.USER_KEEP: '1',
    }

    def inject(self, span_context, headers):
        sampled = '0'
        if span_context.sampling_priority is not None:
            sampled = self._SAMPLING_PRIORITY_MAP[span_context.sampling_priority]

        headers[self.TRACE_ID_KEY] = format_trace_id(span_context.trace_id)
        headers[self.SPAN_ID_KEY] = format_span_id(span_context.span_id)
        headers[self.SAMPLED_KEY] = sampled

    def extract(self, headers):
        trace_id = '0'
        span_id = '0'
        sampled = '0'
        flags = None

        single_header = headers.get(self.SINGLE_HEADER_KEY)

        if single_header:
            # The b3 spec calls for the sampling state to be
            # "deferred", which is unspecified. This concept does not
            # translate to SpanContext, so we set it as recorded.
            sampled = '1'
            fields = single_header.split('-', 4)

            if len(fields) == 1:
                sampled = fields[0]
            elif len(fields) == 2:
                trace_id, span_id = fields
            elif len(fields) == 3:
                trace_id, span_id, sampled = fields
            elif len(fields) == 4:
                trace_id, span_id, sampled, _parent_span_id = fields
            else:
                return Context()
        else:
            trace_id = headers.get(self.TRACE_ID_KEY) or trace_id
            span_id = headers.get(self.SPAN_ID_KEY) or span_id
            sampled = headers.get(self.SAMPLED_KEY) or sampled
            flags = headers.get(self.FLAGS_KEY) or flags

        if sampled in self._SAMPLE_PROPAGATE_VALUES or flags == '1':
            sampling_priority = priority.AUTO_KEEP
        else:
            sampling_priority = priority.AUTO_REJECT

        return Context(
            trace_id=int(trace_id, 16),
            span_id=int(span_id, 16),
            sampling_priority=sampling_priority,
        )


def format_trace_id(trace_id):
    """Format the trace id according to b3 specification."""
    return format(trace_id, '032x')


def format_span_id(span_id):
    """Format the span id according to b3 specification."""
    return format(span_id, '016x')
