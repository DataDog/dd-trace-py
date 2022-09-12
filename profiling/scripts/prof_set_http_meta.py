from collections import defaultdict
import copy
import json
import os

from six import BytesIO

from ddtrace import Span
from ddtrace import config as _config
from ddtrace import tracer
from ddtrace.contrib.trace_utils import set_http_meta


PATH = "/test-benchmark/test/1/"

EXAMPLE_POST_DATA = {f"example_key_{i}": f"example_value{i}" for i in range(100)}

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
    "CONTENT_TYPE": "application/json",
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
    "wsgi.input": BytesIO(bytes(json.dumps(EXAMPLE_POST_DATA), encoding="utf-8")),
    "wsgi.url_scheme": "http",
}


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
    request_headers=COMMON_DJANGO_META,
    response_headers=COMMON_DJANGO_META,
    retries_remain=0,
    raw_uri="http://localhost:8888{}?key1=value1&key2=value2&token="
    "cR8TVoVebF2afssCR16pQeqHcxAlA3867P6zkkUBYDL5Q92kjSGtqptAry1htdlL".format(PATH),
    request_cookies=COOKIES,
    request_path_params={"id": 1},
)

data = copy.deepcopy(DATA_GET)

if os.environ.get("ENABLED"):
    print("Config enabled")
    config = Config(lambda: True)
    tracer._appsec_enabled = True
    _config._appsec_enabled = True
    tracer.configure(api_version="v0.4")

else:
    print("Config disabled")
    config = Config(lambda: False)
    tracer._appsec_enabled = False
    _config._appsec_enabled = False
    tracer.configure(api_version="v0.4")

_Span = Span


def gen_span(name):
    return _Span(name, resource="resource", service="service")


def prof(loops):
    span = gen_span(str("test"))
    span._local_root = gen_span(str("root"))
    for _ in range(loops):
        set_http_meta(span, config, **data)


if __name__ == "__main__":
    print("Run profiling")
    prof(1000000)
    print("End profiling")
