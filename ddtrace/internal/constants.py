"""Legacy re-export shim for a moved module.

``ddtrace.internal.constants`` moved to:

``ddtrace_base.constants``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.constants import _HTTPLIB_NO_TRACE_REQUEST as _HTTPLIB_NO_TRACE_REQUEST  # noqa: F401
from ddtrace_base.constants import _KEEP_PRIORITY_INDEX as _KEEP_PRIORITY_INDEX  # noqa: F401
from ddtrace_base.constants import _PROPAGATION_BEHAVIOR_DEFAULT as _PROPAGATION_BEHAVIOR_DEFAULT  # noqa: F401
from ddtrace_base.constants import _PROPAGATION_BEHAVIOR_IGNORE as _PROPAGATION_BEHAVIOR_IGNORE  # noqa: F401
from ddtrace_base.constants import _PROPAGATION_BEHAVIOR_RESTART as _PROPAGATION_BEHAVIOR_RESTART  # noqa: F401
from ddtrace_base.constants import _PROPAGATION_STYLE_BAGGAGE as _PROPAGATION_STYLE_BAGGAGE  # noqa: F401
from ddtrace_base.constants import _PROPAGATION_STYLE_DEFAULT as _PROPAGATION_STYLE_DEFAULT  # noqa: F401
from ddtrace_base.constants import _PROPAGATION_STYLE_NONE as _PROPAGATION_STYLE_NONE  # noqa: F401
from ddtrace_base.constants import (
    _PROPAGATION_STYLE_W3C_TRACECONTEXT as _PROPAGATION_STYLE_W3C_TRACECONTEXT,  # noqa: F401
)
from ddtrace_base.constants import _REJECT_PRIORITY_INDEX as _REJECT_PRIORITY_INDEX  # noqa: F401
from ddtrace_base.constants import _SERVICE_SOURCE as _SERVICE_SOURCE  # noqa: F401
from ddtrace_base.constants import _WEB_REQUEST_STARTING_DISPATCHED as _WEB_REQUEST_STARTING_DISPATCHED  # noqa: F401
from ddtrace_base.constants import APM_TRACING_ENV as APM_TRACING_ENV  # noqa: F401
from ddtrace_base.constants import AUTO_KEEP as AUTO_KEEP  # noqa: F401
from ddtrace_base.constants import AUTO_REJECT as AUTO_REJECT  # noqa: F401
from ddtrace_base.constants import BAGGAGE_TAG_PREFIX as BAGGAGE_TAG_PREFIX  # noqa: F401
from ddtrace_base.constants import BLOCKED_RESPONSE_HTML as BLOCKED_RESPONSE_HTML  # noqa: F401
from ddtrace_base.constants import BLOCKED_RESPONSE_JSON as BLOCKED_RESPONSE_JSON  # noqa: F401
from ddtrace_base.constants import COLLECTOR_MAX_SIZE_PER_SPAN as COLLECTOR_MAX_SIZE_PER_SPAN  # noqa: F401
from ddtrace_base.constants import COMPONENT as COMPONENT  # noqa: F401
from ddtrace_base.constants import CONTAINER_ID_HEADER_NAME as CONTAINER_ID_HEADER_NAME  # noqa: F401
from ddtrace_base.constants import CONTAINER_TAGS_HASH as CONTAINER_TAGS_HASH  # noqa: F401
from ddtrace_base.constants import DD_TRACE_BAGGAGE_MAX_BYTES as DD_TRACE_BAGGAGE_MAX_BYTES  # noqa: F401
from ddtrace_base.constants import DD_TRACE_BAGGAGE_MAX_ITEMS as DD_TRACE_BAGGAGE_MAX_ITEMS  # noqa: F401
from ddtrace_base.constants import (
    DD_TRACE_TRACESTATE_ITEM_MAX_CHARS as DD_TRACE_TRACESTATE_ITEM_MAX_CHARS,  # noqa: F401
)
from ddtrace_base.constants import DD_TRACE_TRACESTATE_MAX_BYTES as DD_TRACE_TRACESTATE_MAX_BYTES  # noqa: F401
from ddtrace_base.constants import DD_TRACE_TRACESTATE_MAX_ITEMS as DD_TRACE_TRACESTATE_MAX_ITEMS  # noqa: F401
from ddtrace_base.constants import DEFAULT_BUFFER_SIZE as DEFAULT_BUFFER_SIZE  # noqa: F401
from ddtrace_base.constants import DEFAULT_MAX_PAYLOAD_SIZE as DEFAULT_MAX_PAYLOAD_SIZE  # noqa: F401
from ddtrace_base.constants import DEFAULT_PROCESSING_INTERVAL as DEFAULT_PROCESSING_INTERVAL  # noqa: F401
from ddtrace_base.constants import DEFAULT_REUSE_CONNECTIONS as DEFAULT_REUSE_CONNECTIONS  # noqa: F401
from ddtrace_base.constants import DEFAULT_SAMPLING_RATE_LIMIT as DEFAULT_SAMPLING_RATE_LIMIT  # noqa: F401
from ddtrace_base.constants import DEFAULT_SCHEME_PORTS as DEFAULT_SCHEME_PORTS  # noqa: F401
from ddtrace_base.constants import DEFAULT_SERVICE_NAME as DEFAULT_SERVICE_NAME  # noqa: F401
from ddtrace_base.constants import DEFAULT_TIMEOUT as DEFAULT_TIMEOUT  # noqa: F401
from ddtrace_base.constants import ENTITY_ID_HEADER_NAME as ENTITY_ID_HEADER_NAME  # noqa: F401
from ddtrace_base.constants import EXPERIMENTAL_FEATURES as EXPERIMENTAL_FEATURES  # noqa: F401
from ddtrace_base.constants import EXTERNAL_ENV_ENVIRONMENT_VARIABLE as EXTERNAL_ENV_ENVIRONMENT_VARIABLE  # noqa: F401
from ddtrace_base.constants import EXTERNAL_ENV_HEADER_NAME as EXTERNAL_ENV_HEADER_NAME  # noqa: F401
from ddtrace_base.constants import FLASK_ENDPOINT as FLASK_ENDPOINT  # noqa: F401
from ddtrace_base.constants import FLASK_RESOURCE_FULL as FLASK_RESOURCE_FULL  # noqa: F401
from ddtrace_base.constants import FLASK_URL_RULE as FLASK_URL_RULE  # noqa: F401
from ddtrace_base.constants import FLASK_VIEW_ARGS as FLASK_VIEW_ARGS  # noqa: F401
from ddtrace_base.constants import HIGHER_ORDER_TRACE_ID_BITS as HIGHER_ORDER_TRACE_ID_BITS  # noqa: F401
from ddtrace_base.constants import HTTP_REQUEST_BLOCKED as HTTP_REQUEST_BLOCKED  # noqa: F401
from ddtrace_base.constants import HTTP_REQUEST_BODY as HTTP_REQUEST_BODY  # noqa: F401
from ddtrace_base.constants import HTTP_REQUEST_COOKIE_NAME as HTTP_REQUEST_COOKIE_NAME  # noqa: F401
from ddtrace_base.constants import HTTP_REQUEST_COOKIE_VALUE as HTTP_REQUEST_COOKIE_VALUE  # noqa: F401
from ddtrace_base.constants import HTTP_REQUEST_HEADER as HTTP_REQUEST_HEADER  # noqa: F401
from ddtrace_base.constants import HTTP_REQUEST_HEADER_NAME as HTTP_REQUEST_HEADER_NAME  # noqa: F401
from ddtrace_base.constants import HTTP_REQUEST_PARAMETER as HTTP_REQUEST_PARAMETER  # noqa: F401
from ddtrace_base.constants import HTTP_REQUEST_PATH as HTTP_REQUEST_PATH  # noqa: F401
from ddtrace_base.constants import HTTP_REQUEST_PATH_PARAMETER as HTTP_REQUEST_PATH_PARAMETER  # noqa: F401
from ddtrace_base.constants import HTTP_REQUEST_QUERY as HTTP_REQUEST_QUERY  # noqa: F401
from ddtrace_base.constants import HTTP_REQUEST_UPGRADED as HTTP_REQUEST_UPGRADED  # noqa: F401
from ddtrace_base.constants import LAST_DD_PARENT_ID_KEY as LAST_DD_PARENT_ID_KEY  # noqa: F401
from ddtrace_base.constants import LOG_ATTR_ENV as LOG_ATTR_ENV  # noqa: F401
from ddtrace_base.constants import LOG_ATTR_SERVICE as LOG_ATTR_SERVICE  # noqa: F401
from ddtrace_base.constants import LOG_ATTR_SPAN_ID as LOG_ATTR_SPAN_ID  # noqa: F401
from ddtrace_base.constants import LOG_ATTR_TRACE_ID as LOG_ATTR_TRACE_ID  # noqa: F401
from ddtrace_base.constants import LOG_ATTR_VALUE_EMPTY as LOG_ATTR_VALUE_EMPTY  # noqa: F401
from ddtrace_base.constants import LOG_ATTR_VALUE_ZERO as LOG_ATTR_VALUE_ZERO  # noqa: F401
from ddtrace_base.constants import LOG_ATTR_VERSION as LOG_ATTR_VERSION  # noqa: F401
from ddtrace_base.constants import MAX_INT_64BITS as MAX_INT_64BITS  # noqa: F401
from ddtrace_base.constants import MAX_UINT_64BITS as MAX_UINT_64BITS  # noqa: F401
from ddtrace_base.constants import MESSAGING_BATCH_COUNT as MESSAGING_BATCH_COUNT  # noqa: F401
from ddtrace_base.constants import MESSAGING_DESTINATION_NAME as MESSAGING_DESTINATION_NAME  # noqa: F401
from ddtrace_base.constants import MESSAGING_MESSAGE_ID as MESSAGING_MESSAGE_ID  # noqa: F401
from ddtrace_base.constants import MESSAGING_OPERATION as MESSAGING_OPERATION  # noqa: F401
from ddtrace_base.constants import MESSAGING_SYSTEM as MESSAGING_SYSTEM  # noqa: F401
from ddtrace_base.constants import MIN_INT_64BITS as MIN_INT_64BITS  # noqa: F401
from ddtrace_base.constants import PROBABILISTIC_SAMPLING_MECHANISMS as PROBABILISTIC_SAMPLING_MECHANISMS  # noqa: F401
from ddtrace_base.constants import PROCESS_TAGS as PROCESS_TAGS  # noqa: F401
from ddtrace_base.constants import PROPAGATED_HASH as PROPAGATED_HASH  # noqa: F401
from ddtrace_base.constants import PROPAGATION_STYLE_ALL as PROPAGATION_STYLE_ALL  # noqa: F401
from ddtrace_base.constants import PROPAGATION_STYLE_B3_MULTI as PROPAGATION_STYLE_B3_MULTI  # noqa: F401
from ddtrace_base.constants import PROPAGATION_STYLE_B3_SINGLE as PROPAGATION_STYLE_B3_SINGLE  # noqa: F401
from ddtrace_base.constants import PROPAGATION_STYLE_DATADOG as PROPAGATION_STYLE_DATADOG  # noqa: F401
from ddtrace_base.constants import REQUEST_PATH_PARAMS as REQUEST_PATH_PARAMS  # noqa: F401
from ddtrace_base.constants import RESPONSE_HEADERS as RESPONSE_HEADERS  # noqa: F401
from ddtrace_base.constants import SAMPLING_DECISION_MAKER_INHERITED as SAMPLING_DECISION_MAKER_INHERITED  # noqa: F401
from ddtrace_base.constants import SAMPLING_DECISION_MAKER_RESOURCE as SAMPLING_DECISION_MAKER_RESOURCE  # noqa: F401
from ddtrace_base.constants import SAMPLING_DECISION_MAKER_SERVICE as SAMPLING_DECISION_MAKER_SERVICE  # noqa: F401
from ddtrace_base.constants import SAMPLING_DECISION_TRACE_TAG_KEY as SAMPLING_DECISION_TRACE_TAG_KEY  # noqa: F401
from ddtrace_base.constants import SAMPLING_HASH_MODULO as SAMPLING_HASH_MODULO  # noqa: F401
from ddtrace_base.constants import SAMPLING_KNUTH_FACTOR as SAMPLING_KNUTH_FACTOR  # noqa: F401
from ddtrace_base.constants import SAMPLING_MECHANISM_TO_PRIORITIES as SAMPLING_MECHANISM_TO_PRIORITIES  # noqa: F401
from ddtrace_base.constants import SPAN_API_DATADOG as SPAN_API_DATADOG  # noqa: F401
from ddtrace_base.constants import SPAN_API_OPENTRACING as SPAN_API_OPENTRACING  # noqa: F401
from ddtrace_base.constants import SPAN_API_OTEL as SPAN_API_OTEL  # noqa: F401
from ddtrace_base.constants import SPAN_EVENTS_HAS_EXCEPTION as SPAN_EVENTS_HAS_EXCEPTION  # noqa: F401
from ddtrace_base.constants import SPAN_EVENTS_KEY as SPAN_EVENTS_KEY  # noqa: F401
from ddtrace_base.constants import SPAN_LINK_KIND as SPAN_LINK_KIND  # noqa: F401
from ddtrace_base.constants import SPAN_LINKS_KEY as SPAN_LINKS_KEY  # noqa: F401
from ddtrace_base.constants import STATUS_403_TYPE_AUTO as STATUS_403_TYPE_AUTO  # noqa: F401
from ddtrace_base.constants import TRACE_SOURCE_PROPAGATION_KEY as TRACE_SOURCE_PROPAGATION_KEY  # noqa: F401
from ddtrace_base.constants import USER_AGENT_HEADER as USER_AGENT_HEADER  # noqa: F401
from ddtrace_base.constants import USER_KEEP as USER_KEEP  # noqa: F401
from ddtrace_base.constants import USER_REJECT as USER_REJECT  # noqa: F401
from ddtrace_base.constants import W3C_DD_LIST_MEMBER_MAX_CHARS as W3C_DD_LIST_MEMBER_MAX_CHARS  # noqa: F401
from ddtrace_base.constants import W3C_TRACEPARENT_KEY as W3C_TRACEPARENT_KEY  # noqa: F401
from ddtrace_base.constants import W3C_TRACESTATE_KEY as W3C_TRACESTATE_KEY  # noqa: F401
from ddtrace_base.constants import W3C_TRACESTATE_ORIGIN_KEY as W3C_TRACESTATE_ORIGIN_KEY  # noqa: F401
from ddtrace_base.constants import W3C_TRACESTATE_PARENT_ID_KEY as W3C_TRACESTATE_PARENT_ID_KEY  # noqa: F401
from ddtrace_base.constants import (
    W3C_TRACESTATE_SAMPLING_PRIORITY_KEY as W3C_TRACESTATE_SAMPLING_PRIORITY_KEY,  # noqa: F401
)
from ddtrace_base.constants import Constant_Class as Constant_Class  # noqa: F401
from ddtrace_base.constants import SamplingMechanism as SamplingMechanism  # noqa: F401
from ddtrace_base.constants import TraceSource as TraceSource  # noqa: F401
