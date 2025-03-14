from functools import cache
from functools import partial
import sys
import sysconfig
from threading import current_thread
import uuid

from ddtrace.debugging import DynamicInstrumentation
from ddtrace.debugging._config import di_config
from ddtrace.debugging._function.discovery import FunctionDiscovery
from ddtrace.debugging._probe.model import LiteralTemplateSegment
from ddtrace.debugging._probe.model import LogFunctionProbe
from ddtrace.debugging._probe.model import ProbeEvaluateTimingForMethod
from ddtrace.debugging._signal.snapshot import DEFAULT_CAPTURE_LIMITS
from ddtrace.debugging._signal.snapshot import Snapshot
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.module import origin
from ddtrace.internal.module import register_post_run_module_hook
from ddtrace.internal.wrapping import wrap


di_config.enabled = True
DynamicInstrumentation.enable()


stdlib_path = sysconfig.get_path("stdlib")
platstdlib_path = sysconfig.get_path("platstdlib")
purelib_path = sysconfig.get_path("purelib")
platlib_path = sysconfig.get_path("platlib")

SNAPSHOT_MAP = {}

GIT_COMMIT_SHA = 'git.commit.sha'
GIT_REPOSITORY_URL = 'git.repository_url'

@cache
def is_stdlib(filename):
    # type: (str) -> bool
    return (
        filename.startswith(stdlib_path)
        or filename.startswith(platstdlib_path)
        and not (filename.startswith(purelib_path) or filename.startswith(platlib_path))
    )


class ModuleCollector(ModuleWatchdog):
    def __init__(self):
        super().__init__()
        self._seen_modules = set()
        self._tracked_modules = set()
        self._tracer = None
        register_post_run_module_hook(self.after_import)

    def after_import(self, module):
        if module in self._seen_modules:
            return

        self._seen_modules.add(module)

        if is_stdlib(origin(module)):
            return

        self._tracked_modules.add(module)


    @classmethod
    def add_uuid_to_snapshot_map(cls, name, probe):
        line_no = str(probe.frame.f_lineno)
        my_len = len('tests/test_regression.py')
        filename = probe.frame.f_code.co_filename[-my_len:]
        SNAPSHOT_MAP[name] = {"id": probe.uuid, "line":line_no, "file": filename}

    @classmethod
    def _trace(cls, f, args, kwargs):
        with cls._instance._tracer.trace(
            # name=f.__code__.co_name, resource=f.__code__.co_qualname, service=f.__module__
            name=f.__code__.co_name, resource=f.__qualname__, service=f.__module__
        ) as span:
            collector = DynamicInstrumentation._instance._collector
            message = f.__code__.co_name
            probe = LogFunctionProbe(
                probe_id=str(uuid.uuid4()),
                version=0,
                tags={GIT_REPOSITORY_URL: "git@github.com:Datadog/flask.git" , GIT_COMMIT_SHA: "f61172b8dd3f962d33f25c50b2f5405e90ceffa5"},
                module=f.__module__,
                # func_qname=f.__code__.co_qualname,
                func_qname=f.__qualname__,
                template=message,
                segments=[LiteralTemplateSegment(message)],
                take_snapshot=True,
                limits=DEFAULT_CAPTURE_LIMITS,
                condition=None,
                condition_error_rate=0.0,
                rate=float("inf"),
                evaluate_at=ProbeEvaluateTimingForMethod.EXIT,
            )
            signal = Snapshot(
                probe=probe,
                frame=sys._getframe(1),
                thread=current_thread(),
                trace_context=span,
            )
            my_partial = partial(cls.add_uuid_to_snapshot_map, message)
            collector._add_callback(my_partial)
            # SNAPSHOT_MAP[message] = probe.uuid
            with collector.attach(signal):
                return f(*args, **kwargs)

    @classmethod
    def instrument(cls, tracer):
        if not cls._instance:
            return

        cls._instance._tracer = tracer

        for module in cls._instance._tracked_modules:
            # Use function discovery to instrument everything
            fd = FunctionDiscovery.from_module(module)
            for f in set(fd._fullname_index.values()):
                if f.__code__.co_filename != origin(module):
                    continue
                wrap(f, cls._trace)
