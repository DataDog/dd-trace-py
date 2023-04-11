import os


def post_fork(server, worker):
    # Set lower defaults for ensuring profiler collect is run
    if os.environ.get("PERF_PROFILER_ENABLED") == "True":
        os.environ.update(
            {"DD_PROFILING_ENABLED": "1", "DD_PROFILING_API_TIMEOUT": "0.1", "DD_PROFILING_UPLOAD_INTERVAL": "10"}
        )
    if os.environ.get("PERF_TELEMETRY_METRICS_ENABLED") == "True":
        os.environ.update({"_DD_TELEMETRY_METRICS_ENABLED": "1"})
    # This will not work with gevent workers as the gevent hub has not been
    # initialized when this hook is called.
    if os.environ.get("PERF_TRACER_ENABLED") == "True":
        import ddtrace.bootstrap.sitecustomize  # noqa


def post_worker_init(worker):
    # If profiling enabled but not tracer than only run auto script for profiler
    if os.environ.get("PERF_PROFILER_ENABLED") == "True" and os.environ.get("PERF_TRACER_ENABLED") == "False":
        import ddtrace.profiling.auto  # noqa


bind = "0.0.0.0:8000"
worker_class = "sync"
workers = 1
wsgi_app = "app:app"
pidfile = "gunicorn.pid"
