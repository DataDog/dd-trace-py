from ddtrace.trace import tracer


def application(environ, start_response):
    with tracer.trace("test.uwsgi.prefork") as span:
        body = ("trace-id=%d" % span.trace_id).encode()

    start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))])
    return [body]
