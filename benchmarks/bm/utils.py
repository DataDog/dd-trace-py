import contextlib
from functools import partial
from io import BytesIO
import json
import os
import random
import string

from ddtrace import __version__ as ddtrace_version
from ddtrace._trace.span import Span
from ddtrace.internal import telemetry
from ddtrace.trace import TraceFilter


_Span = Span

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

# DEV: 1.x dropped tracer positional argument
if ddtrace_version.split(".")[0] == "0":
    _Span = partial(_Span, None)


class _DropTraces(TraceFilter):
    def process_trace(self, trace):
        return


def drop_traces(tracer):
    tracer.configure(trace_processors=[_DropTraces()])


def drop_telemetry_events():
    # Avoids sending instrumentation telemetry payloads to the agent
    try:
        telemetry.telemetry_writer.stop()
        telemetry.telemetry_writer.reset_queues()
        telemetry.telemetry_writer.enable()
    except AttributeError:
        # telemetry.telemetry_writer is not defined in this version of dd-trace-py
        # Telemetry events will not be mocked!
        pass


def gen_span(name):
    return _Span(name, resource="resource", service="service")


def gen_tags(scenario):
    tag_values = [rands(size=scenario.ltags) for _ in range(scenario.ntags)]
    tag_keys = [rands(size=scenario.ltags) for _ in range(scenario.ntags)]
    tags = {tag_keys[i]: tag_values[i] for i in range(len(tag_keys))}
    return tags


def gen_metrics(scenario):
    metric_keys = [rands(size=16) for _ in range(scenario.nmetrics)]
    metric_values = [random.randint(0, 2**16) for _ in range(scenario.nmetrics)]
    tags = {metric_keys[i]: metric_values[i] for i in range(len(metric_keys))}
    return tags


def random_w_n_digits(lmetrics):
    range_start = 10 ** (lmetrics - 1)
    range_end = (10**lmetrics) - 1
    return random.randint(range_start, range_end)


def rands(size=6, chars=string.ascii_uppercase + string.digits):
    return "".join(random.choice(chars) for _ in range(size))


@contextlib.contextmanager
def override_env(env):
    """
    Temporarily override ``os.environ`` with provided values::

        >>> with self.override_env(dict(DD_TRACE_DEBUG=True)):
            # Your test
    """
    # Copy the full original environment
    original = dict(os.environ)

    # Update based on the passed in arguments
    os.environ.update(env)
    try:
        yield
    finally:
        # Full clear the environment out and reset back to the original
        os.environ.clear()
        os.environ.update(original)
