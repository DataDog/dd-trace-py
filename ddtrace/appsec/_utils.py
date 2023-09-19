import os

from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._constants import API_SECURITY
from ddtrace.constants import APPSEC_ENV
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.http import _get_blocked_template  # noqa
from ddtrace.internal.utils.http import parse_form_multipart  # noqa
from ddtrace.internal.utils.http import parse_form_params  # noqa
from ddtrace.settings import _config as config


log = get_logger(__name__)


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
