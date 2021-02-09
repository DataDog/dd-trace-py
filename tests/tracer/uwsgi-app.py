import logging
import os

from ddtrace import tracer


# Enabling debug mode manually to capture metrics sent from runtime worker
logging.basicConfig()
log = logging.getLogger()
log.level = logging.DEBUG

tracer.configure(
    collect_metrics=os.environ.get("DD_RUNTIME_METRICS_ENABLED", 0) == "1", dogstatsd_url="udp://localhost:38125"
)


def application(env, start_response):
    with tracer.trace("uwsgi-app"):
        start_response("200 OK", [("Content-Type", "text/html")])
        return [b"Hello World"]
