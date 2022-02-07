import errno
import json
import os.path
from typing import TYPE_CHECKING

import attr

from ddtrace.appsec._ddwaf import DDWaf
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.ext import SpanTypes
from ddtrace.gateway import ADDRESSES
from ddtrace.gateway import Gateway
from ddtrace.internal.logger import get_logger
from ddtrace.internal.processor import SpanProcessor
from ddtrace.utils.formats import get_env


if TYPE_CHECKING:
    from ddtrace import Span

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES = os.path.join(ROOT_DIR, "rules.json")

log = get_logger(__name__)


def get_rules():
    return get_env("appsec", "rules", default=DEFAULT_RULES)


COLLECTED_REQUEST_HEADERS = [
    "accept",
    "accept-encoding",
    "accept-language",
    "content-encoding",
    "content-language",
    "content-length",
    "content-type",
    "forwarded",
    "forwarded-for",
    "host",
    "true-client-ip",
    "user-agent",
    "via",
    "x-client-ip",
    "x-cluster-client-ip",
    "x-forwarded",
    "x-forwarded-for",
    "x-real-ip"
]


def _set_headers(span, headers):
    for k in headers:
        low = k.lower()
        if low in COLLECTED_REQUEST_HEADERS:
            span._set_str_tag(low, headers[k])


@attr.s(eq=False)
class AppSecSpanProcessor(SpanProcessor):

    rules = attr.ib(type=str, factory=get_rules)
    _ddwaf = attr.ib(type=DDWaf, default=None)
    _gateway = attr.ib(type=Gateway, default=None)

    @property
    def enabled(self):
        return self._ddwaf is not None

    def __attrs_post_init__(self):
        # type: () -> None
        if self._ddwaf is None:
            try:
                with open(self.rules, "r") as f:
                    rules = json.load(f)
            except EnvironmentError as err:
                if err.errno == errno.ENOENT:
                    log.error(
                        "[DDAS-0001-03] AppSec could not read the rule file %s. Reason: file does not exist", self.rules
                    )
                else:
                    # TODO: try to log reasons
                    log.error("[DDAS-0001-03] AppSec could not read the rule file %s.", self.rules)
                raise
            except json.decoder.JSONDecodeError:
                log.error(
                    "[DDAS-0001-03] AppSec could not read the rule file %s. Reason: invalid JSON file", self.rules
                )
                raise
            except Exception:
                # TODO: try to log reasons
                log.error("[DDAS-0001-03] AppSec could not read the rule file %s.", self.rules)
                raise
            try:
                self._ddwaf = DDWaf(rules)
            except ValueError:
                # Partial of DDAS-0005-00
                log.warning("[DDAS-0005-00] WAF initialization failed")
                raise

    def setup(self, gateway):
        # type: (Gateway) -> None
        self._gateway = gateway
        for address in self._ddwaf.required_data:
            gateway.mark_needed(address)
        # we always need the request headers
        gateway.mark_needed(ADDRESSES.SERVER_REQUEST_HEADERS_NO_COOKIES.value)

    def on_span_start(self, span):
        # type: (Span) -> None
        pass

    def on_span_finish(self, span):
        # type: (Span) -> None
        if span.span_type != SpanTypes.WEB.value:
            return
        span.set_metric("_dd.appsec.enabled", 1.0)
        span._set_str_tag("_dd.runtime_family", "python")
        store = span.store  # since we are on the 'web' span, the store is here!
        if "kept_addresses" not in store:
            return
        data = store["kept_addresses"]
        if ADDRESSES.SERVER_REQUEST_HEADERS_NO_COOKIES.value in data:
            _set_headers(span, data[ADDRESSES.SERVER_REQUEST_HEADERS_NO_COOKIES.value])
        log.debug("[DDAS-001-00] Executing AppSec In-App WAF with parameters: %s", data)
        res = self._ddwaf.run(data)  # res is a serialized json
        if res is not None:
            # Partial DDAS-011-00
            log.debug("[DDAS-011-00] AppSec In-App WAF returned: %s", res)
            span._set_str_tag("appsec.event", "true")
            span._set_str_tag("_dd.appsec.json", '{"triggers":%s}' % (res,))
            span.set_tag(MANUAL_KEEP_KEY)
