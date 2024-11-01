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

class AWSPayloadTagging:
    _INCOMPLETE_TAG = "_dd.payload_tags_incomplete" # Set to True if MAX_TAGS is reached

    _REDACTION_PATHS_DEFAULTS = [
        # SNS
        "$..Attributes.KmsMasterKeyId",
        "$..Attributes.Token",
        # EventBridge
        "$..AuthParameters.OAuthParameters.OAuthHttpParameters.HeaderParameters[*].Value",
        "$..AuthParameters.OAuthParameters.OAuthHttpParameters.QueryStringParameters[*].Value",
        "$..AuthParameters.OAuthParameters.OAuthHttpParameters.BodyParameters[*].Value",
        "$..AuthParameters.InvocationHttpParameters.HeaderParameters[*].Value",
        "$..AuthParameters.InvocationHttpParameters.QueryStringParameters[*].Value",
        "$..AuthParameters.InvocationHttpParameters.BodyParameters[*].Value",
        "$..Targets[*].RedshiftDataParameters.Sql",
        "$..Targets[*].RedshiftDataParameters.Sqls",
        "$..Targets[*].AppSyncParameters.GraphQLOperation",
        # // S3
        "$..SSEKMSKeyId",
        "$..SSEKMSEncryptionContext",
    ]

    _REQUEST_REDACTION_PATHS_DEFAULTS = [
        # Sns
        "$..Attributes.PlatformCredential",
        "$..Attributes.PlatformPrincipal",
        "$..AWSAccountId",
        "$..Endpoint",
        "$..Token",
        "$..OneTimePassword",
        "$..phoneNumber",
        "$..PhoneNumber",
        # EventBridge
        "$..AuthParameters.BasicAuthParameters.Password",
        "$..AuthParameters.OAuthParameters.ClientParameters.ClientSecret",
        "$..AuthParameters.ApiKeyAuthParameters.ApiKeyValue",
        # S3
        "$..SSECustomerKey",
        "$..CopySourceSSECustomerKey",
        "$..RestoreRequest.OutputLocation.S3.Encryption.KMSKeyId"
    ]

    _RESPONSE_REDACTION_PATHS_DEFAULTS = [
        # // Sns
        "$..Endpoints.*.Token",
        "$..PlatformApplication.*.PlatformCredential",
        "$..PlatformApplication.*.PlatformPrincipal",
        "$..Subscriptions.*.Endpoint",
        "$..PhoneNumbers[*].PhoneNumber",
        "$..phoneNumbers[*]",
        # // S3
        "$..Credentials.SecretAccessKey",
        "$..Credentials.SessionToken",
    ]
    def __init__(self):
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
            if self.current_tag_count >= config.botocore.get("payload_tagging_max_tags"):
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
        request_redaction = self._get_redaction_paths_request(config.botocore.get("payload_tagging_request"))
        response_redaction = self._get_redaction_paths_response(config.botocore.get("payload_tagging_response"))
        for path in request_redaction + response_redaction:
            expression = parse(path)
            for match in expression.find(data):
                match.context.value[match.path.fields[0]] = "redacted"

    def _get_redaction_paths_response(self, user_paths: Optional[str]) -> list:
        """
        Get the list of redaction paths, combining defaults with any user-provided JSONPaths.
        """
        if user_paths and user_paths != "all": # "all" is a special value that just enables the expansion and uses defaults
            return self._RESPONSE_REDACTION_PATHS_DEFAULTS + self._REDACTION_PATHS_DEFAULTS + user_paths.replace(" ", "").split(',')
        return self._RESPONSE_REDACTION_PATHS_DEFAULTS + self._REDACTION_PATHS_DEFAULTS

    def _get_redaction_paths_request(self, user_paths: Optional[str]) -> list:
        """
        Get the list of redaction paths, combining defaults with any user-provided JSONPaths.
        """
        if user_paths and user_paths != "all": # "all" is a special value that just enables the expansion and uses defaults
            return self._REQUEST_REDACTION_PATHS_DEFAULTS + self._REDACTION_PATHS_DEFAULTS + user_paths.replace(" ", "").split(',')
        return self._REQUEST_REDACTION_PATHS_DEFAULTS + self._REDACTION_PATHS_DEFAULTS

    def _tag_object(self, span: Span, key: str, obj: Any, depth: int = 0) -> None:
        """
        Expands the current key and value into a span tag
        """
        if self.current_tag_count >= config.botocore.get("payload_tagging_max_tags"):
            span.set_tag(self._INCOMPLETE_TAG, True)
            return    
        if obj is None:
            self.current_tag_count += 1
            span.set_tag(key, obj)
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
