import base64
import gzip
import json
import sys
from typing import TYPE_CHECKING

from ddtrace._tracing._limits import MAX_SPAN_META_VALUE_LEN
from ddtrace.appsec import _processor as appsec_processor
from ddtrace.appsec._asm_request_context import add_context_callback
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import remove_context_callback
from ddtrace.appsec._constants import API_SECURITY
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.internal.logger import get_logger
from ddtrace.internal.metrics import Metrics
from ddtrace.internal.service import Service
from ddtrace.settings.asm import config as asm_config


if TYPE_CHECKING:
    from typing import Optional


log = get_logger(__name__)
metrics = Metrics(namespace="datadog.api_security")
_sentinel = object()


class TooLargeSchemaException(Exception):
    pass


class APIManager(Service):
    COLLECTED = [
        ("REQUEST_HEADERS_NO_COOKIES", API_SECURITY.REQUEST_HEADERS_NO_COOKIES, dict),
        ("REQUEST_COOKIES", API_SECURITY.REQUEST_COOKIES, dict),
        ("REQUEST_QUERY", API_SECURITY.REQUEST_QUERY, dict),
        ("REQUEST_PATH_PARAMS", API_SECURITY.REQUEST_PATH_PARAMS, dict),
        ("REQUEST_BODY", API_SECURITY.REQUEST_BODY, None),
        ("RESPONSE_HEADERS_NO_COOKIES", API_SECURITY.RESPONSE_HEADERS_NO_COOKIES, dict),
        ("RESPONSE_BODY", API_SECURITY.RESPONSE_BODY, None),
    ]

    _instance = None  # type: Optional[APIManager]

    SAMPLE_START_VALUE = 1.0 - sys.float_info.epsilon

    @classmethod
    def enable(cls):
        # type: () -> None
        if cls._instance is not None:
            log.debug("%s already enabled", cls.__name__)
            return

        log.debug("Enabling %s", cls.__name__)
        metrics.enable()
        cls._instance = cls()
        cls._instance.start()
        log.debug("%s enabled", cls.__name__)

    @classmethod
    def disable(cls):
        # type: () -> None
        if cls._instance is None:
            log.debug("%s not enabled", cls.__name__)
            return

        log.debug("Disabling %s", cls.__name__)
        cls._instance.stop()
        cls._instance = None
        metrics.disable()
        log.debug("%s disabled", cls.__name__)

    def __init__(self):
        # type: () -> None
        super(APIManager, self).__init__()

        self.current_sampling_value = self.SAMPLE_START_VALUE
        self._schema_meter = metrics.get_meter("schema")
        log.debug("%s initialized", self.__class__.__name__)

    def _stop_service(self):
        # type: () -> None
        remove_context_callback(self._schema_callback, global_callback=True)

    def _start_service(self):
        # type: () -> None
        add_context_callback(self._schema_callback, global_callback=True)

    def _should_collect_schema(self, env):
        method = env.waf_addresses.get(SPAN_DATA_NAMES.REQUEST_METHOD)
        route = env.waf_addresses.get(SPAN_DATA_NAMES.REQUEST_ROUTE)
        sample_rate = asm_config._api_security_sample_rate
        # Framework is not fully supported
        if not method or not route:
            log.debug("unsupported groupkey for api security [method %s] [route %s]", bool(method), bool(route))
            return False
        # Rate limit per route
        self.current_sampling_value += sample_rate
        if self.current_sampling_value >= 1.0:
            self.current_sampling_value -= 1.0
            return True
        return False

    def _schema_callback(self, env):
        from ddtrace.appsec._utils import _appsec_apisec_features_is_active

        if env.span is None or not _appsec_apisec_features_is_active():
            return
        root = env.span._local_root or env.span
        if not root or any(meta_name in root._meta for _, meta_name, _ in self.COLLECTED):
            return

        try:
            if not self._should_collect_schema(env):
                return
        except Exception:
            log.warning("Failed to sample request for schema generation", exc_info=True)

        # we need the request content type on the span
        try:
            headers = env.waf_addresses.get(SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, _sentinel)
            if headers is not _sentinel:
                appsec_processor._set_headers(root, headers, kind="request")
        except Exception:
            log.debug("Failed to enrich request span with headers", exc_info=True)

        waf_payload = {}
        for address, _, transform in self.COLLECTED:
            value = env.waf_addresses.get(SPAN_DATA_NAMES[address], _sentinel)
            if value is _sentinel:
                log.debug("no value for %s", address)
                continue
            if transform is not None:
                value = transform(value)
            waf_payload[address] = value
        if waf_payload:
            waf_payload["PROCESSOR_SETTINGS"] = {"extract-schema": True}
            result = call_waf_callback(waf_payload)
            if result is None:
                return
            for meta, schema in result.items():
                b64_gzip_content = b""
                try:
                    b64_gzip_content = base64.b64encode(
                        gzip.compress(json.dumps(schema, separators=",:").encode())
                    ).decode()
                    if len(b64_gzip_content) >= MAX_SPAN_META_VALUE_LEN:
                        raise TooLargeSchemaException
                    root._meta[meta] = b64_gzip_content
                except Exception as e:
                    self._schema_meter.increment("errors", tags={"exc": e.__class__.__name__, "address": address})
                    self._log_limiter.limit(
                        log.warning,
                        "Failed to get schema from %r [schema length=%d]:\n%s",
                        address,
                        len(b64_gzip_content),
                        repr(value)[:256],
                        exc_info=True,
                    )
        self._schema_meter.increment("spans")
