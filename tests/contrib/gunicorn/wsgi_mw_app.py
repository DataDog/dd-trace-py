from ddtrace import tracer
from ddtrace.contrib.wsgi import DDWSGIMiddleware
from tests.webclient import PingFilter


tracer.configure(
    settings={
        "FILTERS": [PingFilter()],
    }
)


def simple_app(environ, start_response):
    if environ["RAW_URI"] == "/shutdown":
        tracer.shutdown()

    data = b"Hello, World!\n"
    start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(data)))])
    return iter([data])


app = DDWSGIMiddleware(simple_app)
