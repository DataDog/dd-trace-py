import base64
import collections
import gzip
import json
import time
from typing import Optional

from ddtrace._trace._limits import MAX_SPAN_META_VALUE_LEN
from ddtrace.appsec._constants import API_SECURITY
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._trace_utils import _asm_manual_keep
import ddtrace.constants as constants
from ddtrace.internal import logger as ddlogger
from ddtrace.internal.service import Service
from ddtrace.settings.asm import config as asm_config


log = ddlogger.get_logger(__name__)
API_SECURITY_LOGS = "api_security_callback"
ddlogger.set_tag_rate_limit(API_SECURITY_LOGS, ddlogger.HOUR)

_sentinel = object()


# Max number of endpoint hashes to keep in the hashtable
MAX_HASHTABLE_SIZE = 4096

M_INFINITY = float("-inf")


class TooLargeSchemaException(Exception):
    pass


class APIManager(Service):
    BLOCK_COLLECTED = [
        ("REQUEST_HEADERS_NO_COOKIES", API_SECURITY.REQUEST_HEADERS_NO_COOKIES, dict),
        ("REQUEST_COOKIES", API_SECURITY.REQUEST_COOKIES, dict),
        ("REQUEST_QUERY", API_SECURITY.REQUEST_QUERY, dict),
        ("REQUEST_PATH_PARAMS", API_SECURITY.REQUEST_PATH_PARAMS, dict),
        ("REQUEST_BODY", API_SECURITY.REQUEST_BODY, None),
    ]
    COLLECTED = BLOCK_COLLECTED + [
        ("RESPONSE_HEADERS_NO_COOKIES", API_SECURITY.RESPONSE_HEADERS_NO_COOKIES, dict),
        ("RESPONSE_BODY", API_SECURITY.RESPONSE_BODY, lambda f: f()),
    ]

    _instance: Optional["APIManager"] = None

    @classmethod
    def enable(cls) -> None:
        if cls._instance is not None:
            log.debug("%s already enabled", cls.__name__)
            return

        asm_config._api_security_active = True
        log.debug("Enabling %s", cls.__name__)
        cls._instance = cls()
        cls._instance.start()

        log.debug("%s enabled", cls.__name__)

    @classmethod
    def disable(cls) -> None:
        if cls._instance is None:
            log.debug("%s not enabled", cls.__name__)
            return

        asm_config._api_security_active = False
        log.debug("Disabling %s", cls.__name__)
        cls._instance.stop()
        cls._instance = None
        log.debug("%s disabled", cls.__name__)

    def __init__(self) -> None:
        super(APIManager, self).__init__()

        log.debug("%s initialized", self.__class__.__name__)
        self._hashtable: collections.OrderedDict[int, float] = collections.OrderedDict()

        from ddtrace.appsec import _processor as appsec_processor
        import ddtrace.appsec._asm_request_context as _asm_request_context
        import ddtrace.appsec._metrics as _metrics

        self._asm_context = _asm_request_context
        self._appsec_processor = appsec_processor
        self._metrics = _metrics

    def _stop_service(self) -> None:
        self._asm_context.remove_context_callback(self._schema_callback, global_callback=True)
        self._hashtable.clear()

    def _start_service(self) -> None:
        self._asm_context.add_context_callback(self._schema_callback, global_callback=True)

    def _should_collect_schema(self, env, priority: int) -> Optional[bool]:
        """
        Rate limit per route.

        Returns:
            None: if missing route, method or status
            False: if sampled
            True: if we should collect
        """
        if priority <= 0 and asm_config._apm_tracing_enabled:
            return False

        method = env.waf_addresses.get(SPAN_DATA_NAMES.REQUEST_METHOD)
        route = env.waf_addresses.get(SPAN_DATA_NAMES.REQUEST_ROUTE)
        status = env.waf_addresses.get(SPAN_DATA_NAMES.RESPONSE_STATUS)
        # Framework is not fully supported
        if method is None or route is None or status is None:
            log.debug(
                "unsupported groupkey for api security [method %s] [route %s] [status %s]",
                bool(method),
                bool(route),
                bool(status),
            )
            return None
        end_point_hash = hash((route, method, status))
        current_time = time.monotonic()
        previous_time = self._hashtable.get(end_point_hash, M_INFINITY)
        if previous_time >= current_time - asm_config._api_security_sample_delay:
            return False
        if previous_time is M_INFINITY:
            if len(self._hashtable) >= MAX_HASHTABLE_SIZE:
                self._hashtable.popitem(last=False)
        else:
            self._hashtable.move_to_end(end_point_hash)
        self._hashtable[end_point_hash] = current_time
        return True

    def _schema_callback(self, env):
        if env.span is None or not asm_config._api_security_feature_active:
            return
        root = env.span._local_root or env.span
        collected = self.BLOCK_COLLECTED if env.blocked else self.COLLECTED
        if not root or any(meta_name in root._meta for _, meta_name, _ in collected):
            return

        try:
            # check both current span and root span for sampling priority
            # if sampling has not yet run for the span, we default to treating it as sampled
            if root.context.sampling_priority is None and env.span.context.sampling_priority is None:
                priorities = (1,)
            else:
                priorities = (root.context.sampling_priority or 0, env.span.context.sampling_priority or 0)
            # if any of them is set to USER_KEEP or USER_REJECT, we should respect it
            if constants.USER_KEEP in priorities:
                priority = constants.USER_KEEP
            elif constants.USER_REJECT in priorities:
                priority = constants.USER_REJECT
            else:
                priority = max(priorities)
            should_collect = self._should_collect_schema(env, priority)
            if should_collect is None:
                self._metrics._report_api_security(False, 0)
                return
            if not should_collect:
                return
        except Exception:
            extra = {"product": "appsec", "exec_limit": 6, "more_info": ":sample_request_failure"}
            log.warning(API_SECURITY_LOGS, extra=extra, exc_info=True)
            return

        waf_payload = {"PROCESSOR_SETTINGS": {"extract-schema": True}}
        for address, _, transform in collected:
            if not asm_config._api_security_parse_response_body and address == "RESPONSE_BODY":
                continue
            value = env.waf_addresses.get(SPAN_DATA_NAMES[address], _sentinel)
            if value is _sentinel:
                log.debug("no value for %s", address)
                continue
            if transform is not None:
                value = transform(value)
            waf_payload[address] = value

        result = self._asm_context.call_waf_callback(waf_payload)
        if result is None:
            return
        nb_schemas = 0
        for meta, schema in result.api_security.items():
            b64_gzip_content = b""
            try:
                b64_gzip_content = base64.b64encode(
                    gzip.compress(json.dumps(schema, separators=(",", ":")).encode())
                ).decode()
                if len(b64_gzip_content) >= MAX_SPAN_META_VALUE_LEN:
                    raise TooLargeSchemaException
                root._meta[meta] = b64_gzip_content
                nb_schemas += 1
            except Exception:
                extra = {"product": "appsec", "exec_limit": 6, "more_info": f":schema_failure:{meta}"}
                log.warning(API_SECURITY_LOGS, extra=extra, exc_info=True)
        env.api_security_reported = nb_schemas
        self._metrics._report_api_security(True, nb_schemas)

        # If we have a schema and APM tracing is disabled, force keep the trace
        if nb_schemas > 0 and not asm_config._apm_tracing_enabled:
            _asm_manual_keep(root)
