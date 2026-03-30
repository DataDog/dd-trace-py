from typing import TYPE_CHECKING
from typing import Any
from typing import Optional

import wandb
from wandb.sdk.lib import runid
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.telemetry import get_config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.trace import tracer


if TYPE_CHECKING:
    from wandb import Run

config._add(
    "wandb",
    dict(drop_in=get_config("DD_TRACE_WANDB_DROP_IN_REPLACEMENT", default=True, modifier=asbool)),
)

_MISSING = None
_WANDB_METHODS = {}


def get_version() -> str:
    return str(getattr(wandb, "__version__", ""))


def _supported_versions() -> dict[str, str]:
    return {"wandb": "*"}


def _ddtrace_wandb_init(*args, **kwargs):
    return _make_datadog_wandb_run(project=kwargs.get("project"), init_kwargs=kwargs)


def _ddtrace_wandb_log(*args, **kwargs):
    return None


def _ddtrace_wandb_login(*args, **kwargs):
    return None


def _create_run_span(project: Optional[str], init_kwargs: dict[str, object], run_id: str) -> Span:
    run_span = tracer.start_span(
        "wandb.run",
        span_type=SpanTypes.WORKER,
    )
    if project:
        run_span._set_attribute("wandb.project", str(project))

    if run_id:
        run_span._set_attribute("wandb.run_id", str(run_id))

    config_data = init_kwargs.get("config")
    if isinstance(config_data, dict):
        for key, value in config_data.items():
            run_span._set_attribute("wandb.config.%s" % str(key), str(value))

    return run_span


def _create_log_span(run_span: Span, project: Optional[str], run_id: str, data: dict[str, Any]):
    log_span = tracer.start_span(
        "wandb.log",
        child_of=run_span,
        span_type=SpanTypes.WORKER,
    )
    if project:
        log_span._set_attribute("wandb.project", project)
    log_span._set_attribute("wandb.run_id", str(run_id))
    for key, value in (data or {}).items():
        log_span._set_attribute("wandb.log.%s" % str(key), str(value))
    return log_span


class _DatadogWandbRun:
    """Datadog-owned run object returned by stubbed ``wandb.init``."""

    def __init__(self, run_span: Span, project: Optional[str], run_id: str) -> None:
        self._run_span = run_span
        self._project = project
        self.id = run_id
        self._finished = False

    def _finish_run_span(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._run_span.finish()

    def __enter__(self) -> "_DatadogWandbRun":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._run_span.set_exc_info(exc_type, exc_val, exc_tb)
        self._finish_run_span()
        return None

    def log(self, data: dict[str, Any], *args: Any, **kwargs: Any):
        log_span = _create_log_span(self._run_span, self._project, str(self.id), data)
        log_span.finish()
        return None

    def finish(self, *args: Any, **kwargs: Any):
        self._finish_run_span()
        return None


def _make_datadog_wandb_run(project: Optional[str], init_kwargs: dict[str, object]) -> _DatadogWandbRun:
    run_id = runid.generate_id()
    run_span = _create_run_span(project=project, init_kwargs=init_kwargs, run_id=run_id)
    return _DatadogWandbRun(run_span=run_span, project=project, run_id=run_id)


def _traced_wandb_log(wrapped, instance, args, kwargs):
    pass

def _traced_wandb_run_start(wrapped, instance, args, kwargs):
    entity = get_argument_value(args, kwargs, 0, "entity", True)
    project = get_argument_value(args, kwargs, 1, "project", True)
    id = get_argument_value(args, kwargs, 3, "id", True)
    name = get_argument_value(args, kwargs, 4, "name", True)
    notes = get_argument_value(args, kwargs, 5, "notes", True)
    tags = get_argument_value(args, kwargs, 6, "tags", True)
    job_type = get_argument_value(args, kwargs, 12, "job_type", True)

    run: "Run" = wrapped(*args, **kwargs)

    with core.context_with_data(
        "wand.init",
        span_name="run",
        span_type=SpanTypes.WORKER,
        entity=entity,
        project=project,
        id=id or run.id,
        name=name,
        notes=notes,
        tags=tags,
        job_type=job_type,
        dispatch_end_event=False,
    ) as ctx:
        run._dd_ctx = ctx
        return run


def _traced_wandb_run_exit(wrapped, instance, args, kwargs):
    if not getattr(instance, "_dd_ctx", None):
        return wrapped(*args, **kwargs)

    span: Span = instance._dd_ctx.span
    span.set_exc_info(
        get_argument_value(args, kwargs, 0, "exc_type", True),  # pyright: ignore[reportArgumentType]
        get_argument_value(args, kwargs, 1, "exc_val", True),  # pyright: ignore[reportArgumentType]
        get_argument_value(args, kwargs, 2, "exc_tb", True),
    )

    return wrapped(*args, **kwargs)


def _traced_wandb_run_finish(wrapped, instance, args, kwargs):
    if not getattr(instance, "_dd_ctx", None):
        return wrapped(*args, **kwargs)

    ctx: core.ExecutionContext = instance._dd_ctx

    try:
        result = wrapped(*args, **kwargs)

        ctx.dispatch_ended_event()
        return result
    except BaseException as e:
        ctx.dispatch_ended_event(type(e), e, e.__traceback__)
        raise


def patch():
    if getattr(wandb, "_datadog_patch", False):
        return

    setattr(wandb, "_datadog_patch", True)
    if config.wandb.drop_in:
        _WANDB_METHODS["init"] = getattr(wandb, "init")
        _WANDB_METHODS["log"] = getattr(wandb, "log")
        _WANDB_METHODS["login"] = getattr(wandb, "login")

        setattr(wandb, "init", _ddtrace_wandb_init)
        setattr(wandb, "log", _ddtrace_wandb_log)
        setattr(wandb, "login", _ddtrace_wandb_login)
    else:
        _w(wandb, "init", _traced_wandb_run_start)
        _w(wandb.Run, "log", _traced_wandb_log)
        _w(wandb.Run, "_finish", _traced_wandb_run_finish)
        _w(wandb.Run, "__exit__", _traced_wandb_run_exit)


def unpatch():
    if not getattr(wandb, "_datadog_patch", False):
        return

    if config.wandb.drop_in:
        original_init = _WANDB_METHODS.get("init")
        original_log = _WANDB_METHODS.get("log")
        original_login = _WANDB_METHODS.get("login")

        setattr(wandb, "init", original_init)
        setattr(wandb, "log", original_log)
        setattr(wandb, "login", original_login)

        _WANDB_METHODS.clear()
    else:
        _u(wandb, "init")

    setattr(wandb, "_datadog_patch", False)
