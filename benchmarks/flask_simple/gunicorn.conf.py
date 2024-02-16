from bm.di_utils import BMDebugger
from bm.flask_utils import post_fork  # noqa:F401
from bm.flask_utils import post_worker_init  # noqa:F401

from ddtrace.debugging._probe.model import DEFAULT_CAPTURE_LIMITS
from ddtrace.debugging._probe.model import DEFAULT_SNAPSHOT_PROBE_RATE
from ddtrace.debugging._probe.model import LiteralTemplateSegment
from ddtrace.debugging._probe.model import LogLineProbe


# Probes are added only if the BMDebugger is enabled.
probe_id = "bm-test"
BMDebugger.add_probes(
    LogLineProbe(
        probe_id=probe_id,
        version=0,
        tags={},
        source_file="app.py",
        line=17,
        template=probe_id,
        segments=[LiteralTemplateSegment(probe_id)],
        take_snapshot=True,
        limits=DEFAULT_CAPTURE_LIMITS,
        condition=None,
        condition_error_rate=0.0,
        rate=DEFAULT_SNAPSHOT_PROBE_RATE,
    ),
)

bind = "0.0.0.0:8000"
worker_class = "sync"
workers = 4
wsgi_app = "app:app"
pidfile = "gunicorn.pid"
