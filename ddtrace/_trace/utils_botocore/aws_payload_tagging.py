from typing import Any
from typing import Callable
from typing import Dict
from typing import Optional
import json
import copy

from decimal import Decimal
from ddtrace import Span
from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.ext import SpanKind
from ddtrace.ext import aws
from ddtrace.ext import http
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.utils.formats import deep_getattr
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.vendor.jsonpath_ng import parse

MAX_TAGS = 758 # RFC-defined maximum number of allowed tags
INCOMPLETE_TAG = "_dd.payload_tags_incomplete" # Set to True if MAX_TAGS is reached

class AWSPayloadTagging:
    def __init__(self):
        # TODO is this _total_ tags for the span or is this _total_ expanded tags that we added?
        # TODO is this a limit on the backend or something?
        # currently treating this as the maximum number of tags we will add from the JSON
        self.current_tag_count = 0

    def expand_payload_as_tags(self, span: Span, result: Dict[str, Any], key: str) -> None:
        """
        Expands the JSON payload from various AWS services into tags and sets them on the Span.
        """
        if not result:
            return

        redacted_dict = copy.deepcopy(result)
        self._redact_json(redacted_dict, span)

        for key2, value in redacted_dict.items():
            self._tag_object(span, f"{key}.{key2}", value)
            if self.current_tag_count >= MAX_TAGS:
                return

    def _should_try_string(self, obj: Any) -> bool:
        try:
            if isinstance(obj, str) or isinstance(obj, unicode):
                return True
        except NameError:
            if isinstance(obj, bytes):
                return True

        return False

    def _redact_json(self, data: Dict[str, Any], span: Span) -> None:
        """
        Redact sensitive data in the JSON payload based on default and user-provided JSONPath expressions
        """
        # TODO should we cache these paths and generated lists?
        request_redaction = self._get_redaction_paths(config.botocore.get("payload_tagging_request"))
        response_redaction = self._get_redaction_paths(config.botocore.get("payload_tagging_response"))    
        for path in request_redaction + response_redaction:
            expression = parse(path)
            for match in expression.find(data):
                match.context.value[match.path.fields[0]] = "redacted"

    def _get_redaction_paths(self, user_paths: Optional[str]) -> list:
        """
        Get the list of redaction paths, combining defaults with any user-provided JSONPaths.
        """
        # TODO - I'm treating the defaults as the same for request/response
        # Note: these need to be recursive ".." to ensure that we handle batches
        #       maybe we could do this better though without recursive paths?
        defaults = [
            "$..MessageAttributes.*", # TODO remove this one, as it is for testing!
            "$..Attributes.KmsMasterKeyId",
            "$..Attributes.PlatformCredential",
            "$..Attributes.PlatformPrincipal",
            "$..Attributes.Token",
            "$..AWSAccountId",
            "$..Endpoint",
            "$..OneTimePassword",
            "$..phoneNumber",
            "$..PhoneNumber",
            "$..Token"
        ]
        if user_paths and user_paths != "all": # "all" is a special value that just enables the expansion and uses defaults
            return defaults + user_paths.split(',')
        return defaults

    def _tag_object(self, span: Span, key: str, obj: Any, depth: int = 0) -> None:
        """
        Expands the current key and value into a span tag
        """
        if self.current_tag_count >= MAX_TAGS:
            span.set_tag(INCOMPLETE_TAG, True)
            return    
        if obj is None:
            self.current_tag_count += 1
            span.set_tag(key, obj) # TODO does a value of None actually result in a tag?
            return    
        if depth >= config.botocore.get("payload_tagging_max_depth", 10):
            self.current_tag_count += 1
            span.set_tag(key, str(obj)[:5000])  # at the maximum depth - set the tag without further expansion
            return
        depth += 1
        if self._should_try_string(obj):
            try:
                parsed = json.loads(obj)
                self._tag_object(span, key, parsed, depth)
            except ValueError:
                self.current_tag_count += 1
                span.set_tag(key, str(obj)[:5000])
            return    
        if isinstance(obj, (int, float, Decimal)):
            self.current_tag_count += 1
            span.set_tag(key, str(obj))
            return    
        if isinstance(obj, list):
            for k, v in enumerate(obj):
                self._tag_object(span, f"{key}.{k}", v, depth)
            return    
        if hasattr(obj, "items"):
            for k, v in obj.items():
                self._tag_object(span, f"{key}.{k}", v, depth)
            return    
        if hasattr(obj, "to_dict"):
            for k, v in obj.to_dict().items():
                self._tag_object(span, f"{key}.{k}", v, depth)
            return    
        try:
            value_as_str = str(obj)
        except Exception:
            value_as_str = "UNKNOWN"    
        self.current_tag_count += 1
        span.set_tag(key, value_as_str)
