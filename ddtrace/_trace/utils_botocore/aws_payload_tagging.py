from decimal import Decimal
import json
import threading
from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.vendor.jsonpath_ng import parse


_MAX_TAG_VALUE_LENGTH = 5000


class AWSPayloadTagging:
    _INCOMPLETE_TAG = "_dd.payload_tags_incomplete"  # Set to True if MAX_TAGS is reached

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
        "$..RestoreRequest.OutputLocation.S3.Encryption.KMSKeyId",
    ]

    _RESPONSE_REDACTION_PATHS_DEFAULTS = [
        # // Sns
        "$..Endpoints.*.Token",
        "$..PlatformApplication.*.PlatformCredential",
        "$..PlatformApplication.*.PlatformPrincipal",
        "$..Subscriptions[*].Endpoint",
        "$..PhoneNumbers[*].PhoneNumber",
        "$..phoneNumbers[*]",
        # // S3
        "$..Credentials.SecretAccessKey",
        "$..Credentials.SessionToken",
    ]

    def __init__(self):
        self.validated = False
        self.request_redaction_paths = None
        self.response_redaction_paths = None
        # Parsed once on first call and reused; parsing 15+ default paths on every invocation
        # was a meaningful source of overhead.
        self._parsed_request_expressions = []
        self._parsed_response_expressions = []
        # Read from config once during initialization and stored so _tag_object
        # doesn't need to call config.botocore.get() on every recursive invocation.
        self._max_tags: int = 0
        self._max_depth: int = 0
        self._init_lock = threading.Lock()

    def expand_payload_as_tags(self, span: Span, result: dict[str, Any], key: str) -> None:
        """
        Expands the JSON payload from various AWS services into tags and sets them on the Span.
        """
        if not self.validated:
            with self._init_lock:
                if not self.validated:
                    self.request_redaction_paths = self._get_redaction_paths_request()
                    self.response_redaction_paths = self._get_redaction_paths_response()
                    self._parsed_request_expressions = [parse(p) for p in self.request_redaction_paths]
                    self._parsed_response_expressions = [parse(p) for p in self.response_redaction_paths]
                    self._max_tags = config.botocore.get("payload_tagging_max_tags")
                    self._max_depth = config.botocore.get("payload_tagging_max_depth")
                    self.validated = True

        if not self.request_redaction_paths and not self.response_redaction_paths:
            return

        if not result:
            return

        # Run redaction expressions read-only against the original payload. For each match,
        # record (id(parent_container), field_name_or_index) to identify the location of the
        # sensitive value rather than the value itself. This avoids false positives from Python's
        # string interning — two fields holding the same object remain independently addressable.
        #
        # Only the expression set matching the payload side is evaluated — request paths have no
        # business running against response payloads and vice versa.
        # Mixed set of (id(parent), key) tuples for known path types and id(value) ints as a
        # fallback for exotic path types (Slice, Where, etc.). The two forms never collide —
        # an int membership check never matches a tuple entry and vice versa.
        redacted_ids: set = set()
        exprs = (
            self._parsed_request_expressions
            if key.startswith("aws.request")
            else self._parsed_response_expressions
        )
        for expr in exprs:
            for match in expr.find(result):
                path = match.path
                if hasattr(path, "fields") and path.fields:
                    redacted_ids.add((id(match.context.value), path.fields[0]))
                elif hasattr(path, "index"):
                    redacted_ids.add((id(match.context.value), path.index))
                else:
                    # Fallback for Slice, Where, Root, or other exotic match types.
                    # Falls back to value identity — safe for the long credential strings
                    # these paths would realistically target.
                    redacted_ids.add(id(match.value))

        tag_count = 0
        # flatten the payload into span tags
        for key2, value in result.items():
            escaped_sub_key = key2.replace(".", "\\.")
            if (id(result), key2) in redacted_ids:
                span.set_tag(f"{key}.{escaped_sub_key}", "redacted")
                tag_count += 1
            else:
                tag_count = self._tag_object(span, f"{key}.{escaped_sub_key}", value, 0, tag_count, redacted_ids)
            if tag_count >= self._max_tags:
                span.set_tag(self._INCOMPLETE_TAG, "True")
                return

    def _should_json_parse(self, obj: Any) -> bool:
        if isinstance(obj, (str, bytes)):
            return True
        return False

    def _validate_json_paths(self, paths: Optional[str]) -> bool:
        """
        Checks whether paths is "all" or all valid JSONPaths
        """
        if not paths:
            return False  # not enabled

        if paths == "all":
            return True  # enabled, use the defaults

        # otherwise validate that we have valid JSONPaths
        for path in paths.split(","):
            if path:
                # Require JSONPath to start with "$"
                if not path.startswith("$"):
                    return False
                try:
                    parse(path)
                except Exception:
                    return False
            else:
                return False

        return True

    def _get_redaction_paths_response(self) -> list:
        """
        Get the list of redaction paths, combining defaults with any user-provided JSONPaths.
        """
        if not config.botocore.get("payload_tagging_response"):
            return []

        response_redaction = config.botocore.get("payload_tagging_response")
        if self._validate_json_paths(response_redaction):
            if response_redaction == "all":
                return self._RESPONSE_REDACTION_PATHS_DEFAULTS + self._REDACTION_PATHS_DEFAULTS
            return (
                self._RESPONSE_REDACTION_PATHS_DEFAULTS + self._REDACTION_PATHS_DEFAULTS + response_redaction.split(",")
            )

        return []

    def _get_redaction_paths_request(self) -> list:
        """
        Get the list of redaction paths, combining defaults with any user-provided JSONPaths.
        """
        if not config.botocore.get("payload_tagging_request"):
            return []

        request_redaction = config.botocore.get("payload_tagging_request")
        if self._validate_json_paths(request_redaction):
            if request_redaction == "all":
                return self._REQUEST_REDACTION_PATHS_DEFAULTS + self._REDACTION_PATHS_DEFAULTS
            return (
                self._REQUEST_REDACTION_PATHS_DEFAULTS + self._REDACTION_PATHS_DEFAULTS + request_redaction.split(",")
            )

        return []

    def _tag_object(self, span: Span, key: str, obj: Any, depth: int, tag_count: int, redacted_ids: set) -> int:
        """
        Recursively expands the given AWS payload object and adds the values as flattened Span tags.
        It is not expected that AWS Payloads will be deeply nested so the number of recursive calls should be low.
        For example, the following (shortened payload object) becomes:
        {
            "ResponseMetadata": {
                "RequestId": "SOMEID",
                "HTTPHeaders": {
                    "x-amz-request-id": "SOMEID",
                    "content-length": "5",
                }
        }

        =>

        "aws.response.body.RequestId": "SOMEID"
        "aws.response.body.HTTPHeaders.x-amz-request-id": "SOMEID"
        "aws.response.body.HTTPHeaders.content-length": "5"
        """
        # if we've hit the maximum allowed tags, mark the expansion as incomplete
        if tag_count >= self._max_tags:
            span.set_tag(self._INCOMPLETE_TAG, "True")
            return tag_count
        # fallback redaction check for exotic path types stored as id(value) ints
        if id(obj) in redacted_ids:
            span.set_tag(key, "redacted")
            return tag_count + 1
        if obj is None:
            span.set_tag(key, obj)
            return tag_count + 1
        if depth >= self._max_depth:
            span.set_tag(
                key, str(obj)[:_MAX_TAG_VALUE_LENGTH]
            )  # at the maximum depth - set the tag without further expansion
            return tag_count + 1
        depth += 1
        if self._should_json_parse(obj):
            try:
                parsed = json.loads(obj)
                return self._tag_object(span, key, parsed, depth, tag_count, redacted_ids)
            except ValueError:
                span.set_tag(key, str(obj)[:_MAX_TAG_VALUE_LENGTH])
                return tag_count + 1
        if isinstance(obj, (int, float, Decimal)):
            span.set_tag(key, str(obj))
            return tag_count + 1
        if isinstance(obj, list):
            for k, v in enumerate(obj):
                if (id(obj), k) in redacted_ids:
                    span.set_tag(f"{key}.{k}", "redacted")
                    tag_count += 1
                else:
                    tag_count = self._tag_object(span, f"{key}.{k}", v, depth, tag_count, redacted_ids)
                if tag_count >= self._max_tags:
                    span.set_tag(self._INCOMPLETE_TAG, "True")
                    break
            return tag_count
        if hasattr(obj, "items"):
            for k, v in obj.items():
                escaped_key = str(k).replace(".", "\\.")
                if (id(obj), k) in redacted_ids:
                    span.set_tag(f"{key}.{escaped_key}", "redacted")
                    tag_count += 1
                else:
                    tag_count = self._tag_object(span, f"{key}.{escaped_key}", v, depth, tag_count, redacted_ids)
                if tag_count >= self._max_tags:
                    span.set_tag(self._INCOMPLETE_TAG, "True")
                    break
            return tag_count
        if hasattr(obj, "to_dict"):
            for k, v in obj.to_dict().items():
                escaped_key = str(k).replace(".", "\\.")
                if (id(obj), k) in redacted_ids:
                    span.set_tag(f"{key}.{escaped_key}", "redacted")
                    tag_count += 1
                else:
                    tag_count = self._tag_object(span, f"{key}.{escaped_key}", v, depth, tag_count, redacted_ids)
                if tag_count >= self._max_tags:
                    span.set_tag(self._INCOMPLETE_TAG, "True")
                    break
            return tag_count
        try:
            value_as_str = str(obj)
        except Exception:
            value_as_str = "UNKNOWN"
        span.set_tag(key, value_as_str)
        return tag_count + 1

