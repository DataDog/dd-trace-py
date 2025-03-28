import inspect
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import TypeVar
import weakref

import dd_trace_api

import ddtrace
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.wrapping.context import WrappingContext


_DD_HOOK_NAME = "dd.hook"
_TRACER_KEY = "Tracer"
_STUB_TO_REAL = weakref.WeakKeyDictionary()
_STUB_TO_REAL[dd_trace_api.tracer] = ddtrace.tracer
log = get_logger(__name__)
T = TypeVar("T")
_FN_PARAMS: Dict[str, List[str]] = dict()


class DDTraceAPITelemetryMetrics:
    INIT_TIME = "init_time"


def _params_for_fn(wrapping_context: WrappingContext, instance: dd_trace_api._Stub, fn_name: str):
    key = f"{instance.__class__.__name__}.{fn_name}"
    if key not in _FN_PARAMS:
        _FN_PARAMS[key] = list(inspect.signature(wrapping_context.__wrapped__).parameters.keys())
    return _FN_PARAMS[key]


class DDTraceAPIWrappingContextBase(WrappingContext):
    def _handle_return(self) -> None:
        stub = self.get_local("self")
        fn_name = self.__frame__.f_code.co_name
        _call_on_real_instance(
            stub,
            fn_name,
            self.get_local("retval"),
            **{param: self.get_local(param) for param in _params_for_fn(self, stub, fn_name) if param != "self"},
        )

    def __return__(self, value: T) -> T:
        """Always return the original value no matter what our instrumentation does"""
        try:
            self._handle_return()
        except Exception:  # noqa: E722
            log.debug("Error handling instrumentation return", exc_info=True)

        return value


def _proxy_span_arguments(args: List, kwargs: Dict) -> Tuple[List, Dict]:
    """Convert all dd_trace_api.Span objects in the args/kwargs collections to their held ddtrace.Span objects"""

    def convert(arg):
        return _STUB_TO_REAL[arg] if isinstance(arg, dd_trace_api.Span) else arg

    return [convert(arg) for arg in args], {name: convert(kwarg) for name, kwarg in kwargs.items()}


def _call_on_real_instance(
    operand_stub: dd_trace_api._Stub, method_name: str, retval_from_api: Optional[Any], *args: List, **kwargs: Dict
) -> None:
    """
    Call `method_name` on the real object corresponding to `operand_stub` with `args` and `kwargs` as arguments.

    Store the value that will be returned from the API call we're in the middle of, for the purpose
    of mapping from those Stub objects to their real counterparts.
    """
    args, kwargs = _proxy_span_arguments(args, kwargs)
    retval_from_impl = getattr(_STUB_TO_REAL[operand_stub], method_name)(*args, **kwargs)
    if retval_from_api is not None:
        _STUB_TO_REAL[retval_from_api] = retval_from_impl
    # NB cardinality is limited by the size of the API exposed by ddtrace-api
    metric_name = f"{operand_stub.__class__.__name__}.{method_name}"
    telemetry_writer.add_count_metric(namespace=TELEMETRY_NAMESPACE.DD_TRACE_API, name=metric_name, value=1)


def get_version() -> str:
    return getattr(dd_trace_api, "__version__", "")


def patch(tracer=None):
    if getattr(dd_trace_api, "__datadog_patch", False):
        return
    _STUB_TO_REAL[dd_trace_api.tracer] = tracer

    DDTraceAPIWrappingContextBase(dd_trace_api.Tracer.start_span).wrap()
    DDTraceAPIWrappingContextBase(dd_trace_api.Tracer.trace).wrap()
    DDTraceAPIWrappingContextBase(dd_trace_api.Tracer.current_span).wrap()
    DDTraceAPIWrappingContextBase(dd_trace_api.Tracer.current_root_span).wrap()
    DDTraceAPIWrappingContextBase(dd_trace_api.Span.finish).wrap()
    DDTraceAPIWrappingContextBase(dd_trace_api.Span.set_exc_info).wrap()
    DDTraceAPIWrappingContextBase(dd_trace_api.Span.finish_with_ancestors).wrap()
    DDTraceAPIWrappingContextBase(dd_trace_api.Span.set_tags).wrap()
    DDTraceAPIWrappingContextBase(dd_trace_api.Span.set_traceback).wrap()
    DDTraceAPIWrappingContextBase(dd_trace_api.Span.__enter__).wrap()
    DDTraceAPIWrappingContextBase(dd_trace_api.Span.__exit__).wrap()

    dd_trace_api.__datadog_patch = True


def unpatch():
    if not getattr(dd_trace_api, "__datadog_patch", False):
        return
    dd_trace_api.__datadog_patch = False

    DDTraceAPIWrappingContextBase.extract(dd_trace_api.Tracer.start_span).unwrap()
    DDTraceAPIWrappingContextBase.extract(dd_trace_api.Tracer.trace).unwrap()
    DDTraceAPIWrappingContextBase.extract(dd_trace_api.Tracer.current_span).unwrap()
    DDTraceAPIWrappingContextBase.extract(dd_trace_api.Tracer.current_root_span).unwrap()
    DDTraceAPIWrappingContextBase.extract(dd_trace_api.Span.finish).unwrap()
    DDTraceAPIWrappingContextBase.extract(dd_trace_api.Span.set_exc_info).unwrap()
    DDTraceAPIWrappingContextBase.extract(dd_trace_api.Span.finish_with_ancestors).unwrap()
    DDTraceAPIWrappingContextBase.extract(dd_trace_api.Span.set_tags).unwrap()
    DDTraceAPIWrappingContextBase.extract(dd_trace_api.Span.set_traceback).unwrap()
    DDTraceAPIWrappingContextBase.extract(dd_trace_api.Span.__enter__).unwrap()
    DDTraceAPIWrappingContextBase.extract(dd_trace_api.Span.__exit__).unwrap()

    dd_trace_api.__datadog_patch = False
