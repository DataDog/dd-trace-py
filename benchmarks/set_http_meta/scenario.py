from collections import defaultdict
import copy
from io import BytesIO

import bm as bm
import bm.utils as utils

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


PATH = "/test-benchmark/test/1/"

COMMON_DJANGO_META = {
    "SERVER_PORT": "8000",
    "REMOTE_HOST": "",
    "CONTENT_LENGTH": "",
    "SCRIPT_NAME": "",
    "SERVER_PROTOCOL": "HTTP/1.1",
    "SERVER_SOFTWARE": "WSGIServer/0.2",
    "REQUEST_METHOD": "GET",
    "PATH_INFO": PATH,
    "QUERY_STRING": "func=subprocess.run&cmd=%2Fbin%2Fecho+hello",
    "REMOTE_ADDR": "127.0.0.1",
    "CONTENT_TYPE": "text/plain",
    "HTTP_HOST": "localhost:8000",
    "HTTP_CONNECTION": "keep-alive",
    "HTTP_CACHE_CONTROL": "max-age=0",
    "HTTP_SEC_CH_UA": '"Chromium";v="92", " Not A;Brand";v="99", "Google Chrome";v="92"',
    "HTTP_SEC_CH_UA_MOBILE": "?0",
    "HTTP_UPGRADE_INSECURE_REQUESTS": "1",
    "HTTP_ACCEPT": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
    "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
    "HTTP_SEC_FETCH_SITE": "none",
    "HTTP_SEC_FETCH_MODE": "navigate",
    "HTTP_SEC_FETCH_USER": "?1",
    "HTTP_SEC_FETCH_DEST": "document",
    "HTTP_ACCEPT_ENCODING": "gzip, deflate, br",
    "HTTP_ACCEPT_LANGUAGE": "en-US,en;q=0.9",
    "HTTP_COOKIE": "Pycharm-45729245=449f1b16-fe0a-4623-92bc-418ec418ed4b; Idea-9fdb9ed8="
    "448d4c93-863c-4e9b-a8e7-bbfbacd073d2; csrftoken=cR8TVoVebF2afssCR16pQeqHcxA"
    "lA3867P6zkkUBYDL5Q92kjSGtqptAry1htdlL; _xsrf=2|d4b85683|7e2604058ea673d12dc6604f"
    '96e6e06d|1635869800; username-localhost-8888="2|1:0|10:1637328584|23:username-loca'
    "lhost-8888|44:OWNiOTFhMjg1NDllNDQxY2I2Y2M2ODViMzRjMTg3NGU=|3bc68f938dcc081a9a02e51660"
    '0c0d38b14a3032053a7e16b180839298e25b42"',
    "wsgi.input": BytesIO(),
    "wsgi.url_scheme": "http",
}

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
    request_headers=COMMON_DJANGO_META,
    response_headers=COMMON_DJANGO_META,
    retries_remain=0,
    raw_uri="http://localhost:8888{}?key1=value1&key2=value2&token="
    "cR8TVoVebF2afssCR16pQeqHcxAlA3867P6zkkUBYDL5Q92kjSGtqptAry1htdlL".format(PATH),
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

        span = utils.gen_span(str("test"))
        span._local_root = utils.gen_span(str("root"))

        def bm(loops):
            for _ in range(loops):
                set_http_meta(span, config, **data)

        yield bm
