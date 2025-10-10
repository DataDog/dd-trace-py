# cython: freethreading_compatible=True
from typing import List
from typing import Optional

from ddtrace._trace.processor cimport TraceProcessor
from ddtrace._trace.span import Span
from ddtrace.constants import SPAN_KIND


cdef class PeerServiceProcessor(TraceProcessor):
    cdef object _config
    cdef bint _set_defaults_enabled
    cdef dict _mapping
    
    def __init__(self, peer_service_config):
        self._config = peer_service_config
        self._set_defaults_enabled = self._config.set_defaults_enabled
        self._mapping = self._config.peer_service_mapping

    cpdef process_trace(self, trace: List[Span]):
        cdef str tag_name
        cdef object span, tag
        cdef set enabled_span_kinds
        
        if not trace:
            return

        tag_name = self._config.tag_name

        if not self._set_defaults_enabled:
            for span in trace:
                tag = span.get_tag(tag_name)
                if tag:
                    self._update_peer_service_tags(span, tag)
        else:
            enabled_span_kinds = self._config.enabled_span_kinds
            for span in trace:
                tag = span.get_tag(tag_name)
                if tag or span.get_tag(SPAN_KIND) in enabled_span_kinds:
                    self._update_peer_service_tags(span, tag)
        return trace

    cdef inline void _update_peer_service_tags(self, span: Span, tag: Optional[str]):
        cdef str data_source
        
        if tag:  # If the tag already exists, assume it is user generated
            span.set_tag_str(self._config.source_tag_name, self._config.tag_name)
        else:
            for data_source in self._config.prioritized_data_sources:
                tag = span.get_tag(data_source)
                if tag:
                    span.set_tag_str(self._config.tag_name, tag)
                    span.set_tag_str(self._config.source_tag_name, data_source)
                    break

        if tag and tag in self._mapping:
            span.set_tag_str(self._config.remap_tag_name, tag)
            span.set_tag_str(self._config.tag_name, self._config.peer_service_mapping[tag])
