from ddtrace._trace.processor import TraceProcessor
from ddtrace.constants import SPAN_KIND


class PeerServiceProcessor(TraceProcessor):
    def __init__(self, peer_service_config):
        self._config = peer_service_config
        self._set_defaults_enabled = self._config.set_defaults_enabled
        self._mapping = self._config.peer_service_mapping

    def process_trace(self, trace):
        if not trace:
            return

        traces_to_process = []
        if not self._set_defaults_enabled:
            traces_to_process = filter(lambda x: x.get_tag(self._config.tag_name), trace)
        else:
            traces_to_process = filter(
                lambda x: x.get_tag(self._config.tag_name) or x.get_tag(SPAN_KIND) in self._config.enabled_span_kinds,
                trace,
            )
        any(map(lambda x: self._update_peer_service_tags(x), traces_to_process))

        return trace

    def _update_peer_service_tags(self, span):
        tag = span.get_tag(self._config.tag_name)

        if tag:  # If the tag already exists, assume it is user generated
            span.set_tag_str(self._config.source_tag_name, self._config.tag_name)
        else:
            for data_source in self._config.prioritized_data_sources:
                tag = span.get_tag(data_source)
                if tag:
                    span.set_tag_str(self._config.tag_name, tag)
                    span.set_tag_str(self._config.source_tag_name, data_source)
                    break

        if tag in self._mapping:
            span.set_tag_str(self._config.remap_tag_name, tag)
            span.set_tag_str(self._config.tag_name, self._config.peer_service_mapping[tag])
