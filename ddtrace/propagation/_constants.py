_PROPAGATION_STYLE_DATADOG = "datadog"
_PROPAGATION_STYLE_B3_MULTI = "b3multi"
_PROPAGATION_STYLE_B3_SINGLE = "b3"
_PROPAGATION_STYLE_W3C_TRACECONTEXT = "tracecontext"
_PROPAGATION_STYLE_NONE = "none"
_PROPAGATION_STYLE_DEFAULT = "datadog,tracecontext,baggage"
_PROPAGATION_STYLE_BAGGAGE = "baggage"
_PROPAGATION_STYLE_ALL = (
    _PROPAGATION_STYLE_W3C_TRACECONTEXT,
    _PROPAGATION_STYLE_DATADOG,
    _PROPAGATION_STYLE_B3_MULTI,
    _PROPAGATION_STYLE_B3_SINGLE,
    _PROPAGATION_STYLE_NONE,
    _PROPAGATION_STYLE_BAGGAGE,
)
_PROPAGATION_BEHAVIOR_CONTINUE = "continue"
_PROPAGATION_BEHAVIOR_IGNORE = "ignore"
_PROPAGATION_BEHAVIOR_RESTART = "restart"
_PROPAGATION_BEHAVIOR_DEFAULT = _PROPAGATION_BEHAVIOR_CONTINUE
_W3C_TRACESTATE_KEY = "tracestate"
_W3C_TRACEPARENT_KEY = "traceparent"
_W3C_TRACESTATE_PARENT_ID_KEY = "p"
_W3C_TRACESTATE_ORIGIN_KEY = "o"
_W3C_TRACESTATE_SAMPLING_PRIORITY_KEY = "s"

_DD_TRACE_BAGGAGE_MAX_ITEMS = 64
_DD_TRACE_BAGGAGE_MAX_BYTES = 8192
_BAGGAGE_TAG_PREFIX = "baggage."
# W3C Trace Context tracestate (https://www.w3.org/TR/trace-context/):
# max 32 list-members; vendors SHOULD propagate at most 512 characters (we cap parsing to that size).
_DD_TRACE_TRACESTATE_MAX_ITEMS = 32
_DD_TRACE_TRACESTATE_MAX_BYTES = 512
# Per W3C Trace Context, oversized list-members are preferred targets when truncating by size.
_DD_TRACE_TRACESTATE_ITEM_MAX_CHARS = 128

_SPAN_API_DATADOG = "datadog"
_SPAN_API_OTEL = "otel"

_BLOCKED_RESPONSE_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>You've been blocked</title><style>a,body,div,html,span{margin:0;padding:0;border:0;font-size:100%;font:inherit;vertical-align:baseline}body{background:-webkit-radial-gradient(26% 19%,circle,#fff,#f4f7f9);background:radial-gradient(circle at 26% 19%,#fff,#f4f7f9);display:-webkit-box;display:-ms-flexbox;display:flex;-webkit-box-pack:center;-ms-flex-pack:center;justify-content:center;-webkit-box-align:center;-ms-flex-align:center;align-items:center;-ms-flex-line-pack:center;align-content:center;width:100%;min-height:100vh;line-height:1;flex-direction:column}p{display:block}main{text-align:center;flex:1;display:-webkit-box;display:-ms-flexbox;display:flex;-webkit-box-pack:center;-ms-flex-pack:center;justify-content:center;-webkit-box-align:center;-ms-flex-align:center;align-items:center;-ms-flex-line-pack:center;align-content:center;flex-direction:column}p{font-size:18px;line-height:normal;color:#646464;font-family:sans-serif;font-weight:400}a{color:#4842b7}footer{width:100%;text-align:center}footer p{font-size:16px}.security-response-id{font-size:14px;color:#999;margin-top:20px;font-family:monospace}</style></head><body><main><p>Sorry, you cannot access this page. Please contact the customer service team.</p><p class="security-response-id">Security Response ID: [security_response_id]</p></main><footer><p>Security provided by <a href="https://www.datadoghq.com/product/security-platform/application-security-monitoring/" target="_blank" rel="noopener noreferrer">Datadog</a></p></footer></body></html>"""  # noqa: E501
_BLOCKED_RESPONSE_JSON = """{"errors":[{"title":"You've been blocked","detail":"Sorry, you cannot access this page. Please contact the customer service team. Security provided by Datadog."}],"security_response_id":"[security_response_id]"}"""  # noqa: E501
_HTTP_REQUEST_UPGRADED = "http.upgraded"
