import os

import uwsgi
import uwsgidecorators


@uwsgidecorators.postfork
def application_postfork():
    with open(os.environ["DD_TEST_UWSGI_POSTFORK_PIDS"], "a") as marker:
        marker.write("%d\n" % os.getpid())


def application(environ, start_response):
    from ddtrace.trace import tracer

    with tracer.trace("test.uwsgi.prefork") as span:
        worker_pids = ",".join(str(worker["pid"]) for worker in uwsgi.workers() if worker["pid"])
        body = (
            "pid=%d;workers=%s;ssi=%s;trace-id=%d"
            % (os.getpid(), worker_pids, os.environ.get("_DD_PY_SSI_INJECT"), span.trace_id)
        ).encode()

    start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))])
    return [body]
