import typing as t

from wrapt.importer import when_imported

from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.testing.internal.eval import record_eval_metric
from ddtrace.trace import tracer


if t.TYPE_CHECKING:
    from ddtrace.trace import Span


log = get_logger(__name__)

_FRAMEWORK = "deepeval"
# ``assert_test`` and ``evaluate`` funnel through ``execute_test_cases`` /
# ``a_execute_test_cases``, which they resolve as names imported into the
# ``deepeval.evaluate.evaluate`` module, so we wrap them there. This is more
# robust than wrapping ``assert_test``/``evaluate`` (which deep-copy the metrics
# for async runs) or ``BaseMetric.measure`` (abstract, and replaced per-subclass
# by deepeval's own observability wrappers).
#
# The trace-based ``assert_test(golden=...)`` form measures through a separate
# path (``_assert_test_from_current_trace``, which reads metrics off the live
# ``@observe`` trace rather than this funnel) and is not captured yet.
_MODULE = "deepeval.evaluate.evaluate"


def get_version() -> str:
    try:
        import importlib.metadata as importlib_metadata

        return str(importlib_metadata.version("deepeval"))
    except Exception:
        return ""


def _supported_versions() -> dict[str, str]:
    return {"deepeval": ">=4.0.5"}


def _test_root_span() -> t.Optional["Span"]:
    """Return the active Test Optimization test span, or ``None``.

    Mirrors the selenium integration guard: eval calls run inside the user's
    test body, so the enriched span is the per-test root span (``type=test``).
    """
    try:
        span = tracer.current_root_span()
    except Exception:
        return None
    if span is None or span.get_tag("type") != "test":
        return None
    return span


def _record_results(span: "Span", results: t.Any) -> t.Any:
    """Record every metric from an iterable of deepeval ``TestResult`` objects.

    ``results`` is materialized once so that a one-shot iterator is safely both
    recorded here and returned to deepeval's caller; the materialized value is
    returned.
    """
    if not results:
        return results
    try:
        test_results = list(results)
    except TypeError:
        return results
    for test_result in test_results:
        for md in getattr(test_result, "metrics_data", None) or []:
            record_eval_metric(
                span,
                framework=_FRAMEWORK,
                name=getattr(md, "name", None),
                score=getattr(md, "score", None),
                threshold=getattr(md, "threshold", None),
                passed=getattr(md, "success", None),
                model=getattr(md, "evaluation_model", None),
                cost=getattr(md, "evaluation_cost", None),
                reason=getattr(md, "reason", None),
                error=getattr(md, "error", None),
            )
    return test_results


def _wrapped_execute_test_cases(func: t.Callable[..., t.Any], instance: t.Any, args: t.Any, kwargs: t.Any) -> t.Any:
    span = _test_root_span()
    results = func(*args, **kwargs)
    if span is None:
        return results
    return _record_results(span, results)


def _wrapped_a_execute_test_cases(func: t.Callable[..., t.Any], instance: t.Any, args: t.Any, kwargs: t.Any) -> t.Any:
    # Capture the test span synchronously, before the coroutine runs.
    span = _test_root_span()
    coro = func(*args, **kwargs)
    if span is None:
        return coro

    async def _traced() -> t.Any:
        # Record after all of deepeval's concurrent measurement has completed.
        results = await coro
        return _record_results(span, results)

    return _traced()


# name -> wrapper, applied when the deepeval evaluate module is imported.
_WRAPPERS = (
    ("execute_test_cases", _wrapped_execute_test_cases),
    ("a_execute_test_cases", _wrapped_a_execute_test_cases),
)


def _wrap_module(module: t.Any) -> None:
    for name, wrapper in _WRAPPERS:
        try:
            wrap(module, name, wrapper)
        except Exception:
            log.debug("deepeval: could not wrap %s", name, exc_info=True)


def patch() -> None:
    import deepeval

    if getattr(deepeval, "_datadog_patch", False):
        return
    deepeval._datadog_patch = True

    # ``import deepeval`` transitively imports ``deepeval.evaluate.evaluate``, so
    # this fires immediately; when_imported also handles the deferred case safely.
    when_imported(_MODULE)(_wrap_module)


def unpatch() -> None:
    import deepeval

    if not getattr(deepeval, "_datadog_patch", False):
        return
    deepeval._datadog_patch = False

    try:
        import deepeval.evaluate.evaluate as module
    except ImportError:
        return
    for name, _ in _WRAPPERS:
        try:
            unwrap(module, name)
        except Exception:
            log.debug("deepeval: could not unwrap %s", name, exc_info=True)
