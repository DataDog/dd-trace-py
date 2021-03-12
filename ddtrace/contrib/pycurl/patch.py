import pycurl

from ... import config
from ...ext import SpanTypes
from ...internal.logger import get_logger
from ...pin import Pin
from ...propagation.http import HTTPPropagator
from ...utils.formats import asbool, get_env
from ...vendor import wrapt
from .. import trace_utils

log = get_logger(__name__)

# requests default settings
config._add(
    "pycurl",
    {
        "distributed_tracing": asbool(get_env("pycurl", "distributed_tracing", default=True)),
        # "split_by_domain": asbool(get_env("pycurl", "split_by_domain", default=False)),
    },
)


# https://github.com/curl/curl/blob/44872aefc2d54f297caf2b0cc887df321bc9d791/include/curl/curl.h#L2762
# Available from libcurl >=7.72.0
CURLINFO_EFFECTIVE_METHOD = 0x100000 + 58


class TracedCurl(wrapt.CallableObjectProxy):
    # This is called for Curl.__init__ and returns back a wrapped instance
    def __init__(self, *args, **kwargs):
        super(TracedCurl, self).__init__(*args, **kwargs)
        self._self_last_method_used = "GET"

    def __call__(self, *args, **kwargs):
        instance = TracedCurl(self.__wrapped__(*args, **kwargs))
        Pin(app="pycurl", _config=config.pycurl).onto(instance)
        return instance

    def setopt(self, option, value):
        # In libcurl >=7.72.0 we can call getinfo(CURLINFO_EFFECTIVE_METHOD)
        # however, pycurl does not expose this option and does type checking
        # on calls to `Curl.getinfo` and will raise if it gets an unknown option
        # https://github.com/pycurl/pycurl/blob/5895a70200a5fcde3c56c4a868cc63fef404d682/src/easyinfo.c#L120-L255
        # https://curl.se/libcurl/c/CURLINFO_EFFECTIVE_METHOD.html
        if option == pycurl.HTTPGET:
            self._self_last_method_used = "GET"
        elif option == pycurl.HTTPPOST or option == pycurl.POSTFIELDS:
            self._self_last_method_used = "POST"

        return self.__wrapped__.setopt(option, value)

    def perform(self, *args, **kwargs):
        pin = Pin.get_from(self)
        if not pin or not pin.enabled():
            return self.__wrapped__.perform(*args, **kwargs)

        with pin.tracer.trace("curl.request", span_type=SpanTypes.HTTP) as span:
            if pin._config["distributed_tracing"]:
                headers = dict()
                ctx = pin.tracer.get_call_context()
                HTTPPropagator.inject(ctx, headers)

                curl_headers = []
                for header, value in headers.items():
                    curl_headers.append("{}: {}".format(header, value))

                self.__wrapped__.setopt(pycurl.HTTPHEADER, curl_headers)

            res = self.__wrapped__.perform(*args, **kwargs)

            try:
                trace_utils.set_http_meta(
                    span,
                    config.pycurl,
                    method=self._self_last_method_used,
                    url=self.getinfo(pycurl.EFFECTIVE_URL),
                    status_code=self.getinfo(pycurl.RESPONSE_CODE),
                )
            except Exception:
                log.debug("Failed to fetch pycurl response information", exc_info=True)
            finally:
                return res


def patch():
    if getattr(pycurl, "__datadog_patch", False):
        return
    setattr(pycurl, "__datadog_patch", True)

    setattr(pycurl, "Curl", TracedCurl(pycurl.Curl))


def unpatch():
    if not getattr(pycurl, "__datadog_patch", False):
        return
    setattr(pycurl, "__datadog_patch", False)

    setattr(pycurl, "Curl", pycurl.Curl.__wrapped__)
