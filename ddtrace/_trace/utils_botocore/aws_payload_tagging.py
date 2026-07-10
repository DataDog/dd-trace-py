from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal
import json
import threading
from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.internal.logger import get_logger
from ddtrace.vendor.jsonpath_ng import parse


log = get_logger(__name__)


_MAX_TAG_VALUE_LENGTH = 5000


@dataclass
class _RedactionContext:
    """Redaction state threaded through _tag_object/_tag_children calls."""

    # (id(parent), field_name_or_index) tuples for known path types;
    # id(value) ints as a fallback for exotic path types (Slice, Where, etc.).
    # The two forms never collide — an int check never matches a tuple entry.
    redacted_ids: set = field(default_factory=set)
    # id()s of containers that are ancestors of a redacted field, used by the
    # depth-truncation branch to avoid stringifying objects that would expose secrets.
    ancestor_ids: set = field(default_factory=set)
    # Parsed JSONPath expressions for the active payload side.
    exprs: list = field(default_factory=list)


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
        # S3
        "$..SSEKMSKeyId",
        "$..SSEKMSEncryptionContext",
    ]
    _REQUEST_REDACTION_PATHS_DEFAULTS = [
        # SNS
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
        # SNS
        "$..Endpoints.*.Token",
        "$..PlatformApplication.*.PlatformCredential",
        "$..PlatformApplication.*.PlatformPrincipal",
        "$..Subscriptions[*].Endpoint",
        "$..PhoneNumbers[*].PhoneNumber",
        "$..phoneNumbers[*]",
        # S3
        "$..Credentials.SecretAccessKey",
        "$..Credentials.SessionToken",
    ]

    def __init__(self):
        self.validated = False
        # Parsed once on first call and reused; parsing 15+ default paths on every
        # invocation was a meaningful source of overhead.
        self._parsed_request_expressions = []
        self._parsed_response_expressions = []
        # Read from config once during initialization and stored so _tag_object
        # doesn't need to call config.botocore.get() on every recursive invocation.
        self._max_tags: int = 0
        self._max_depth: int = 0
        self._init_lock = threading.Lock()

    def expand_payload_as_tags(self, span: Span, payload: dict[str, Any], key: str) -> None:
        """
        Expands the JSON payload from various AWS services into tags and sets them on the Span.
        """
        if not self.validated:
            with self._init_lock:
                if not self.validated:
                    # Both sides include all default paths — arbitrary payloads (e.g. SQS
                    # message bodies) can contain any field structure, so request payloads
                    # need response-side defaults and vice versa.
                    all_side_defaults = self._REQUEST_REDACTION_PATHS_DEFAULTS + self._RESPONSE_REDACTION_PATHS_DEFAULTS
                    request_paths = self._get_redaction_paths("payload_tagging_request", all_side_defaults)
                    response_paths = self._get_redaction_paths("payload_tagging_response", all_side_defaults)
                    self._parsed_request_expressions = [parse(p) for p in request_paths]
                    self._parsed_response_expressions = [parse(p) for p in response_paths]
                    self._max_tags = config.botocore.get("payload_tagging_max_tags")
                    self._max_depth = config.botocore.get("payload_tagging_max_depth")
                    self.validated = True

        if not payload:
            return

        # Select the expression set for this payload side. Empty means the feature is
        # disabled for this side — return without tagging to avoid emitting unredacted data.
        exprs = self._parsed_request_expressions if key.startswith("aws.request") else self._parsed_response_expressions
        if not exprs:
            return

        ctx = self._build_redaction_context(payload, exprs)

        tag_count = 0
        for field_name, value in payload.items():
            escaped_name = field_name.replace(".", "\\.")
            tag_key = f"{key}.{escaped_name}"
            if (id(payload), field_name) in ctx.redacted_ids:
                span.set_tag(tag_key, "redacted")
                tag_count += 1
            else:
                tag_count = self._tag_object(span, tag_key, value, 0, tag_count, ctx)
            if tag_count >= self._max_tags:
                span.set_tag(self._INCOMPLETE_TAG, "True")
                return

    def _build_redaction_context(self, payload: Any, exprs: list) -> _RedactionContext:
        """
        Run redaction expressions read-only against payload and return a _RedactionContext.
        Called once for the outer payload and again each time an embedded JSON string is
        parsed, so every object graph gets fresh sets keyed on the correct object identities.
        """
        ctx = _RedactionContext(exprs=exprs)
        for expr in exprs:
            for match in expr.find(payload):
                path = match.path
                if hasattr(path, "fields") and path.fields:
                    for field_name in path.fields:
                        ctx.redacted_ids.add((id(match.context.value), field_name))
                elif hasattr(path, "index"):
                    ctx.redacted_ids.add((id(match.context.value), path.index))
                elif match.value is payload:
                    # Root match ($) — mark every top-level key so the entire payload
                    # is suppressed via the normal location-based check.
                    if hasattr(payload, "keys"):
                        for k in payload:
                            ctx.redacted_ids.add((id(payload), k))
                    else:
                        # Non-mapping root (e.g. a list) — fall back to value identity
                        # so the fallback check in _tag_object catches it.
                        ctx.redacted_ids.add(id(match.value))
                else:
                    # Fallback for Slice, Where, or other exotic match types
                    ctx.redacted_ids.add(id(match.value))

                # Walk the context chain to record every ancestor container id
                current = match
                while current.context is not None:
                    ctx.ancestor_ids.add(id(current.context.value))
                    current = current.context
        return ctx

    def _validate_json_paths(self, paths: Optional[str]) -> bool:
        """
        Checks whether paths is "all" or all valid JSONPaths.
        """
        if not paths:
            return False  # not enabled

        if paths == "all":
            return True  # enabled, use the defaults

        for path in paths.split(","):
            path = path.strip()
            if path:
                if not path.startswith("$"):
                    log.warning(
                        "Invalid JSONPath expression %r in payload tagging config — "
                        "all custom paths must start with '$'. Payload tagging will be "
                        "disabled until the config is corrected.",
                        path,
                    )
                    return False
                try:
                    parse(path)
                except Exception:
                    log.warning(
                        "Failed to parse JSONPath expression %r in payload tagging config. "
                        "Payload tagging will be disabled until the config is corrected.",
                        path,
                    )
                    return False
            else:
                log.warning(
                    "Empty JSONPath expression in payload tagging config (check for "
                    "leading/trailing commas). Payload tagging will be disabled until "
                    "the config is corrected.",
                )
                return False

        return True

    def _get_redaction_paths(self, config_key: str, side_defaults: list) -> list:
        """
        Build the redaction path list for one payload side, combining side-specific and
        shared defaults with any user-provided JSONPaths. Returns an empty list if the
        config value is absent or invalid — disabling tagging on misconfiguration is
        safer than emitting partially-redacted data with unknown custom paths ignored.
        """
        config_value = config.botocore.get(config_key)
        if not config_value:
            return []
        config_value = config_value.strip()
        if not config_value:
            log.warning(
                "Payload tagging config %r is whitespace-only and will be ignored. "
                "Payload tagging will be disabled until the config is corrected.",
                config_key,
            )
            return []
        config_value = config_value.lower() if config_value.lower() == "all" else config_value
        if not self._validate_json_paths(config_value):
            return []
        defaults = side_defaults + self._REDACTION_PATHS_DEFAULTS
        if config_value == "all":
            return defaults
        return defaults + [p.strip() for p in config_value.split(",")]

    def _tag_children(
        self,
        span: Span,
        key: str,
        obj: Any,
        items: Any,
        depth: int,
        tag_count: int,
        ctx: _RedactionContext,
    ) -> int:
        """Iterate key-value pairs from a container, redacting or recursing for each child."""
        for k, v in items:
            escaped_k = str(k).replace(".", "\\.") if isinstance(k, str) else k
            child_key = f"{key}.{escaped_k}"
            if (id(obj), k) in ctx.redacted_ids:
                span.set_tag(child_key, "redacted")
                tag_count += 1
            else:
                tag_count = self._tag_object(span, child_key, v, depth, tag_count, ctx)
            if tag_count >= self._max_tags:
                span.set_tag(self._INCOMPLETE_TAG, "True")
                break
        return tag_count

    def _tag_object(
        self,
        span: Span,
        key: str,
        obj: Any,
        depth: int,
        tag_count: int,
        ctx: _RedactionContext,
    ) -> int:
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
        if tag_count >= self._max_tags:
            span.set_tag(self._INCOMPLETE_TAG, "True")
            return tag_count
        # Fallback redaction check for exotic path types stored as id(value) ints
        if id(obj) in ctx.redacted_ids:
            span.set_tag(key, "redacted")
            return tag_count + 1
        if obj is None:
            span.set_tag(key, obj)
            return tag_count + 1
        if depth >= self._max_depth:
            # If this object is an ancestor of a redacted field, stringifying it would
            # expose sensitive data in clear text — redact the whole subtree instead.
            if id(obj) in ctx.ancestor_ids:
                span.set_tag(key, "redacted")
            else:
                span.set_tag(key, str(obj)[:_MAX_TAG_VALUE_LENGTH])
            return tag_count + 1
        depth += 1
        if isinstance(obj, (str, bytes)):
            try:
                parsed = json.loads(obj)
                # Rebuild the redaction context for the new object graph — the original
                # context is keyed on id()s from the outer payload and won't match the
                # fresh objects created by json.loads.
                new_ctx = self._build_redaction_context(parsed, ctx.exprs)
                return self._tag_object(span, key, parsed, depth, tag_count, new_ctx)
            except ValueError:
                span.set_tag(key, str(obj)[:_MAX_TAG_VALUE_LENGTH])
                return tag_count + 1
        if isinstance(obj, (int, float, Decimal)):
            span.set_tag(key, str(obj))
            return tag_count + 1
        if isinstance(obj, list):
            return self._tag_children(span, key, obj, enumerate(obj), depth, tag_count, ctx)
        if hasattr(obj, "items"):
            return self._tag_children(span, key, obj, obj.items(), depth, tag_count, ctx)
        if hasattr(obj, "to_dict"):
            return self._tag_children(span, key, obj, obj.to_dict().items(), depth, tag_count, ctx)
        try:
            value_as_str = str(obj)
        except Exception:
            value_as_str = "UNKNOWN"
        span.set_tag(key, value_as_str)
        return tag_count + 1
