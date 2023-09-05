import os
from typing import TYPE_CHECKING

from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._constants import API_SECURITY
from ddtrace.constants import APPSEC_ENV
from ddtrace.internal.compat import parse
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.http import _get_blocked_template  # noqa
from ddtrace.settings import _config as config


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any

    from ddtrace.internal.compat import text_type as unicode


log = get_logger(__name__)


def parse_form_params(body):
    # type: (unicode) -> dict[unicode, unicode|list[unicode]]
    """Return a dict of form data after HTTP form parsing"""
    body_params = body.replace("+", " ")
    req_body = dict()  # type: dict[unicode, unicode|list[unicode]]
    for item in body_params.split("&"):
        key, equal, val = item.partition("=")
        if equal:
            key = parse.unquote(key)
            val = parse.unquote(val)
            prev_value = req_body.get(key, None)
            if prev_value is None:
                req_body[key] = val
            elif isinstance(prev_value, list):
                prev_value.append(val)
            else:
                req_body[key] = [prev_value, val]
    return req_body


def parse_form_multipart(body):
    # type: (unicode) -> dict[unicode, Any]
    """Return a dict of form data after HTTP form parsing"""
    import email
    import json

    import xmltodict

    def parse_message(msg):
        if msg.is_multipart():
            res = {
                part.get_param("name", failobj=part.get_filename(), header="content-disposition"): parse_message(part)
                for part in msg.get_payload()
            }
        else:
            content_type = msg.get("Content-Type")
            if content_type in ("application/json", "text/json"):
                res = json.loads(msg.get_payload())
            elif content_type in ("application/xml", "text/xml"):
                res = xmltodict.parse(msg.get_payload())
            elif content_type in ("text/plain", None):
                res = msg.get_payload()
            else:
                res = ""

        return res

    headers = _asm_request_context.get_headers()
    if headers is not None:
        content_type = headers.get("Content-Type")
        msg = email.message_from_string("MIME-Version: 1.0\nContent-Type: %s\n%s" % (content_type, body))
        return parse_message(msg)
    return {}


def parse_response_body(raw_body):
    import json

    import xmltodict

    from ddtrace.appsec._constants import SPAN_DATA_NAMES
    from ddtrace.contrib.trace_utils import _get_header_value_case_insensitive

    if not raw_body:
        return

    if isinstance(raw_body, dict):
        return raw_body

    headers = _asm_request_context.get_waf_address(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES)
    if not headers:
        return
    content_type = _get_header_value_case_insensitive(
        dict(headers),
        "content-type",
    )
    if not content_type:
        return

    def access_body(bd):
        if isinstance(bd, list) and isinstance(bd[0], (str, bytes)):
            bd = bd[0][:0].join(bd)
        if getattr(bd, "decode", False):
            bd = bd.decode("UTF-8", errors="ignore")
        if len(bd) >= API_SECURITY.MAX_PAYLOAD_SIZE:
            raise ValueError("response body larger than 16MB")
        return bd

    req_body = None
    try:
        # TODO handle charset
        if "json" in content_type:
            req_body = json.loads(access_body(raw_body))
        elif "xml" in content_type:
            req_body = xmltodict.parse(access_body(raw_body))
        else:
            return
    except BaseException:
        log.debug("Failed to parse response body", exc_info=True)
    else:
        return req_body


def _appsec_rc_features_is_enabled():
    # type: () -> bool
    if config._remote_config_enabled:
        return APPSEC_ENV not in os.environ
    return False
