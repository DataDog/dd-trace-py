from collections import defaultdict
import copy

import bm as bm
import bm.utils as utils

from ddtrace import config as ddconfig
from ddtrace.contrib.trace_utils import set_http_meta


class Config(defaultdict):
    __header_tag_name = {
        "User-Agent": "http.user_agent",
        "REFERER": "http.referer",
        "Content-Type": "http.content_type",
        "Etag": "http.etag",
    }

    def _header_tag_name(self, header_name):
        return self.__header_tag_name.get(header_name)

    def __getattr__(self, item):
        return self[item]


COOKIES = {"csrftoken": "cR8TVoVebF2afssCR16pQeqHcxAlA3867P6zkkUBYDL5Q92kjSGtqptAry1htdlL"}

DATA_GET = dict(
    method="GET",
    status_code=200,
    status_msg="OK",
    parsed_query={
        "key1": "value1",
        "key2": "value2",
        "token": "cR8TVoVebF2afssCR16pQeqHcxAlA3867P6zkkUBYDL5Q92kjSGtqptAry1htdlL",
    },
    request_headers=utils.COMMON_DJANGO_META,
    response_headers=utils.COMMON_DJANGO_META,
    retries_remain=0,
    raw_uri="http://localhost:8888{}?key1=value1&key2=value2&token="
    "cR8TVoVebF2afssCR16pQeqHcxAlA3867P6zkkUBYDL5Q92kjSGtqptAry1htdlL".format(utils.PATH),
    request_cookies=COOKIES,
    request_path_params={"id": 1},
)


class SetHttpMeta(bm.Scenario):
    allenabled = bm.var_bool()
    useragentvariant = bm.var(type=str)
    obfuscation_disabled = bm.var_bool()
    send_querystring_enabled = bm.var_bool()
    url = bm.var(type=str)
    querystring = bm.var(type=str)
    ip_header = bm.var(type=str)
    ip_disabled = bm.var_bool()

    def run(self):
        # run scenario to also set tags on spans
        if self.allenabled:
            config = Config(lambda: True)
        else:
            config = Config(lambda: False)

        # querystring obfuscation config
        config["trace_query_string"] = self.send_querystring_enabled
        if self.obfuscation_disabled:
            ddconfig._obfuscation_query_string_pattern = None

        data = copy.deepcopy(DATA_GET)
        data["url"] = self.url
        data["query"] = self.querystring

        if self.useragentvariant:
            data["request_headers"][self.useragentvariant] = (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/92.0.4515.159 Safari/537.36"
            )

        if self.ip_header:
            data["request_headers"][self.ip_header] = "8.8.8.8"

        span = utils.gen_span(str("test"))
        span._local_root = utils.gen_span(str("root"))

        def bm(loops):
            with utils.override_env(
                dict(
                    DD_TRACE_CLIENT_IP_HEADER_DISABLED=str(self.ip_disabled),
                    DD_TRACE_CLIENT_IP_HEADER=self.ip_header,
                )
            ):
                for _ in range(loops):
                    set_http_meta(span, config, **data)

        yield bm
