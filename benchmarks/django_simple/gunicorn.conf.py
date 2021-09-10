import os


def post_fork(server, worker):
    # Set lower defaults for ensuring profiler collect is run
    if os.environ.get("PERF_PROFILER_ENABLED") == "1":
        os.environ.update(
            {"DD_PROFILING_ENABLED": "1", "DD_PROFILING_API_TIMEOUT": "0.1", "DD_PROFILING_UPLOAD_INTERVAL": "10"}
        )
    # This will not work with gevent workers as the gevent hub has not been
    # initialized when this hook is called.
    if os.environ.get("PERF_TRACER_ENABLED") == "1":
        import ddtrace.bootstrap.sitecustomize  # noqa


def post_worker_init(worker):
    # If profiling enabled but not tracer than only run auto script for profiler
    if os.environ.get("PERF_PROFILER_ENABLED") == "1" and os.environ.get("PERF_TRACER_ENABLED") == "0":
        import ddtrace.profiling.auto  # noqa


bind = "0.0.0.0:8000"
worker_class = "sync"
workers = 1
wsgi_app = "django.core.wsgi:get_wsgi_application()"
pidfile = "gunicorn.pid"
raw_env = ["DJANGO_SETTINGS_MODULE=app"]
