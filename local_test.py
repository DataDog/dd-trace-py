from datetime import datetime
from functools import reduce
from itertools import dropwhile
from multiprocessing import Lock
from multiprocessing import Pool
from operator import or_
import os
from subprocess import PIPE
from subprocess import run
from sys import argv
from sys import exit


test_files = [
    "tests/appsec/__init__.py",
    "tests/appsec/iast/__init__.py",
    "tests/appsec/iast/test_overhead_control_engine.py",
    "tests/appsec/iast/test_processor.py",
    "tests/appsec/iast/test_weak_ciper.py",
    "tests/appsec/iast/test_weak_hash.py",
    "tests/appsec/test_ddwaf_fuzz.py",
    "tests/appsec/test_remoteconfiguration.py",
    "tests/benchmarks/test_glob_matching.py",
    "tests/benchmarks/test_span_id.py",
    "tests/benchmarks/test_tracer.py",
    "tests/debugging/function/__init__.py",
    "tests/debugging/function/test_discovery.py",
    "tests/debugging/function/test_store.py",
    "tests/debugging/probe/__init__.py",
    "tests/debugging/probe/test_model.py",
    "tests/debugging/probe/test_registry.py",
    "tests/debugging/probe/test_remoteconfig.py",
    "tests/debugging/probe/test_status.py",
    "tests/debugging/py35/__init__.py",
    "tests/debugging/py35/test_async.py",
    "tests/debugging/snapshot/__init__.py",
    "tests/debugging/snapshot/test_collector.py",
    "tests/debugging/test_config.py",
    "tests/debugging/test_encoding.py",
    "tests/debugging/test_expressions.py",
    "tests/debugging/test_uploader.py",
    "tests/integration/__init__.py",
    "tests/integration/test_context_snapshots.py",
    "tests/integration/test_integration_snapshots.py",
    "tests/integration/test_propagation.py",
    "tests/integration/test_trace_stats.py",
    "tests/internal/__init__.py",
    "tests/internal/py35/__init__.py",
    "tests/internal/py35/test_wrapping.py",
    "tests/internal/py36/__init__.py",
    "tests/internal/py36/test_wrapping.py",
    "tests/internal/test_codeowners.py",
    "tests/internal/test_glob_matcher.py",
    "tests/internal/test_injection.py",
    "tests/internal/test_metrics.py",
    "tests/internal/test_safety.py",
    "tests/internal/test_utils_http.py",
    "tests/internal/test_utils_version.py",
    "tests/internal/test_wrapping.py",
    "tests/opentracer/__init__.py",
    "tests/opentracer/core/__init__.py",
    "tests/opentracer/core/test_dd_compatibility.py",
    "tests/opentracer/core/test_span.py",
    "tests/opentracer/core/test_span_context.py",
    "tests/opentracer/core/test_utils.py",
    "tests/opentracer/test_tracer_asyncio.py",
    "tests/opentracer/test_tracer_gevent.py",
    "tests/profiling/collector/__init__.py",
    "tests/profiling/collector/test_asyncio.py",
    "tests/profiling/collector/test_collector.py",
    "tests/profiling/collector/test_memalloc.py",
    "tests/profiling/collector/test_stack_asyncio.py",
    "tests/profiling/collector/test_threading_asyncio.py",
    "tests/profiling/collector/test_traceback.py",
    "tests/profiling/exporter/test_file.py",
    "tests/profiling/exporter/test_packages.py",
    "tests/profiling/test_nogevent.py",
    "tests/profiling/test_scheduler.py",
    "tests/profiling/test_traceback.py",
    "tests/profiling/test_uwsgi.py",
    "tests/tracer/__init__.py",
    "tests/tracer/runtime/__init__.py",
    "tests/tracer/runtime/test_container.py",
    "tests/tracer/runtime/test_metric_collectors.py",
    "tests/tracer/runtime/test_metrics.py",
    "tests/tracer/runtime/test_runtime_id.py",
    "tests/tracer/runtime/test_tag_collectors.py",
    "tests/tracer/test_agent.py",
    "tests/tracer/test_compat.py",
    "tests/tracer/test_constants.py",
    "tests/tracer/test_context.py",
    "tests/tracer/test_env_vars.py",
    "tests/tracer/test_filters.py",
    "tests/tracer/test_global_config.py",
    "tests/tracer/test_hooks.py",
    "tests/tracer/test_hostname.py",
    "tests/tracer/test_http.py",
    "tests/tracer/test_instance_config.py",
    "tests/tracer/test_memory_leak.py",
    "tests/tracer/test_periodic.py",
    "tests/tracer/test_pin.py",
    "tests/tracer/test_processors.py",
    "tests/tracer/test_rand.py",
    "tests/tracer/test_rate_limiter.py",
    "tests/tracer/test_sampling_decision.py",
    "tests/tracer/test_service.py",
    "tests/tracer/test_settings.py",
    "tests/tracer/test_single_span_sampling_rule.py",
    "tests/tracer/test_single_span_sampling_rules.py",
    "tests/tracer/test_sma.py",
    "tests/tracer/test_tagset.py",
    "tests/tracer/test_utils.py",
    "tests/tracer/test_version.py",
    # tests/vendor/__init__.py \
    # tests/vendor/msgpack/test_buffer.py \
    # tests/vendor/msgpack/test_case.py \
    # tests/vendor/msgpack/test_except.py \
    # tests/vendor/msgpack/test_limits.py \
    # tests/vendor/msgpack/test_pack.py \
    # tests/vendor/msgpack/test_read_size.py \
    # tests/vendor/msgpack/test_seq.py \
    # tests/vendor/msgpack/test_subtype.py \
    # tests/vendor/msgpack/test_unpack.py \
    # tests/vendor/test_contextvars.py \
    # tests/vendor/test_dogstatsd.py
    # tests/contrib/asyncio/__init__.py \
    # tests/contrib/asyncio/test_helpers.py \
    # tests/contrib/asyncio/test_patch.py \
    # tests/contrib/asyncio/test_propagation.py \
    # tests/contrib/asyncio/test_tracer.py \
    # tests/contrib/asyncio/test_tracer_safety.py \
    # tests/contrib/bottle/__init__.py \
    # tests/contrib/bottle/test.py \
    # tests/contrib/bottle/test_distributed.py \
    # tests/contrib/celery/test_old_style_task.py \
    # tests/contrib/celery/test_patch.py \
    # tests/contrib/dbapi/__init__.py \
    # tests/contrib/djangorestframework/__init__.py \
    # tests/contrib/djangorestframework/test_djangorestframework.py \
    # tests/contrib/dogpile_cache/__init__.py \
    # tests/contrib/falcon/__init__.py \
    # tests/contrib/falcon/test_distributed_tracing.py \
    # tests/contrib/falcon/test_middleware.py \
    # tests/contrib/flask/__init__.py \
    # tests/contrib/flask/test_blueprint.py \
    # tests/contrib/flask/test_errorhandler.py \
    # tests/contrib/flask/test_flask_appsec.py \
    # tests/contrib/flask/test_flask_helpers.py \
    # tests/contrib/flask/test_hooks.py \
    # tests/contrib/flask/test_idempotency.py \
    # tests/contrib/flask/test_request.py \
    # tests/contrib/flask/test_signals.py \
    # tests/contrib/flask/test_static.py \
    # tests/contrib/flask/test_template.py \
    # tests/contrib/flask/test_views.py \
    # tests/contrib/flask_cache/__init__.py \
    # tests/contrib/flask_cache/test.py \
    # tests/contrib/flask_cache/test_utils.py \
    # tests/contrib/flask_cache/test_wrapper_safety.py \
    # tests/contrib/futures/__init__.py \
    # tests/contrib/futures/test_propagation.py \
    # tests/contrib/gevent/test_provider.py \
    # tests/contrib/grpc/test_grpc_utils.py \
    # tests/contrib/jinja2/__init__.py \
    # tests/contrib/jinja2/test_jinja2.py \
    # tests/contrib/logging/__init__.py \
    # tests/contrib/logging/test_logging.py \
    # tests/contrib/mako/__init__.py \
    # tests/contrib/mako/test_mako.py \
    # tests/contrib/pymemcache/__init__.py \
    # tests/contrib/pymemcache/test_client.py \
    # tests/contrib/pymemcache/test_client_mixin.py \
    # tests/contrib/pymongo/test_spec.py \
    # tests/contrib/pyramid/utils.py \
    # tests/contrib/requests/test_requests_distributed.py \
    # tests/contrib/sanic/test_sanic.py \
    # tests/contrib/sqlalchemy/test_sqlite.py \
    # tests/contrib/sqlite3/__init__.py \
    # tests/contrib/sqlite3/test_sqlite3.py \
    # tests/contrib/tornado/__init__.py \
    # tests/contrib/tornado/test_config.py \
    # tests/contrib/tornado/test_executor_decorator.py \
    # tests/contrib/tornado/test_safety.py \
    # tests/contrib/tornado/test_stack_context.py \
    # tests/contrib/tornado/test_tornado_template.py \
    # tests/contrib/tornado/test_tornado_web.py \
    # tests/contrib/tornado/test_wrap_decorator.py
]

useful_test_files = [
    "tests/appsec/iast/test_overhead_control_engine.py",
    "tests/appsec/iast/test_processor.py",
    "tests/appsec/iast/test_weak_ciper.py",
    "tests/appsec/iast/test_weak_hash.py",
    "tests/appsec/test_remoteconfiguration.py",
    "tests/debugging/test_uploader.py",
    "tests/profiling/collector/test_stack_asyncio.py",
    "tests/profiling/test_traceback.py",
    "tests/tracer/test_rate_limiter.py",
]


TEST_FILE_LOG = "failures_test.log"

lock = Lock()


def test_with_file(filename):
    time = datetime.now()
    cp = run(  # --no-cov to prevent internal errors
        ["python", "-m", "pytest", "-x", "-r", "Ef", "--no-cov", filename],
        stdout=PIPE,
        stderr=PIPE,
        env=os.environ | {"LINES": "80", "COLUMNS": "512", "TERM": "xterm-256color"},
    )
    if cp.returncode:
        print(cp.stdout.decode())
        print(cp.stderr.decode())
    summary = list(
        dropwhile(
            (lambda l: "short test summary info" not in l), (line.strip() for line in cp.stdout.decode().split("\n"))
        )
    )

    failures = [
        line[0] + line[line.index(" ") :] for line in summary if line.startswith("FAILED ") or line.startswith("ERROR ")
    ]
    lock.acquire()
    with open(TEST_FILE_LOG, "a") as log:
        for failure in failures:
            print(*argv[-2:], cp.returncode, failure, file=log)
        if cp.returncode and not failures:
            if cp.returncode == 1:  # Should not happen, logging everything
                print(*argv[-2:], cp.returncode, "X", filename, "UnknownError", file=log)
                print("**STDOUT", file=log)
                print(cp.stdout.decode(), file=log)
                print("**STDERR", file=log)
                print(cp.stderr.decode(), file=log)
            else:
                error_lines = cp.stderr.decode().split("\n")
                print(*argv[-2:], cp.returncode, "E", filename, error_lines[-1], error_lines[0], file=log)

    dtime = datetime.now() - time
    print(f"tested {filename}", dtime)
    lock.release()
    return cp.returncode


def main():
    with Pool(8) as p:
        all_errors = reduce(or_, p.map(test_with_file, useful_test_files))

    exit(1 if all_errors else 0)


if __name__ == "__main__":
    main()
