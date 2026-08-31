"""Generic schema for recording evaluation-testing-framework results on test spans.

This module defines a framework-agnostic convention (mirroring the benchmark
convention in ``ddtrace/contrib/internal/pytest_benchmark/``) for attaching the
results of LLM/eval testing frameworks (deepeval, and others in the future) to
Test Optimization test spans, so they can be filtered and aggregated in the
Test Optimization Explorer.

An eval test is marked with ``test.type=eval`` and each measured metric is
recorded under a top-level ``eval.<metric>.<field>`` namespace, where numeric
values (score, threshold, cost, token usage) become span *metrics* and labels
(status, model, reason, ...) become span *tags*.

Framework integrations should call :func:`record_eval_metric` once per metric,
passing the current test root span (obtained via ``tracer.current_root_span()``
and guarded by ``span.get_tag("type") == "test"``).
"""

import re
import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.testing.internal.test_data import TestTag
from ddtrace.testing.internal.test_data import TestType


if t.TYPE_CHECKING:
    from ddtrace.trace import Span


log = get_logger(__name__)

# Top-level namespace, following the benchmark convention (``benchmark.*``).
EVAL_NAMESPACE = "eval"

# Test-level roll-up keys.
EVAL_FRAMEWORK = "eval.framework"  # tag: producing framework, e.g. "deepeval"
EVAL_METRICS = "eval.metrics"  # tag: comma-joined metric slugs present on the test
EVAL_METRIC_COUNT = "eval.metric_count"  # metric: number of eval metrics recorded

# Per-metric status values.
STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_ERROR = "error"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: t.Optional[str]) -> str:
    if not name:
        return "metric"
    slug = _SLUG_RE.sub("_", str(name).strip().lower()).strip("_")
    return slug or "metric"


def _dedupe_slug(span: "Span", slug: str) -> str:
    """Return a slug that is not yet used on this span.

    ``eval.<slug>.name`` is always written for a recorded metric, so it is a
    reliable presence sentinel for de-duplicating repeated metric names within
    the same test (``answer_relevancy``, ``answer_relevancy_2``, ...).
    """
    if span.get_tag(f"{EVAL_NAMESPACE}.{slug}.name") is None:
        return slug
    i = 2
    while span.get_tag(f"{EVAL_NAMESPACE}.{slug}_{i}.name") is not None:
        i += 1
    return f"{slug}_{i}"


def _set_tag(span: "Span", key: str, value: t.Optional[t.Any]) -> None:
    if value is None:
        return
    text = str(value)
    if text:
        span.set_tag(key, text)


def _set_metric(span: "Span", key: str, value: t.Optional[t.Any]) -> None:
    if value is None:
        return
    try:
        span.set_metric(key, float(value))
    except (TypeError, ValueError):
        log.debug("eval: could not record numeric metric %s=%r", key, value)


def _update_rollups(span: "Span", slug: str) -> None:
    count = span.get_metric(EVAL_METRIC_COUNT) or 0
    span.set_metric(EVAL_METRIC_COUNT, count + 1)
    existing = span.get_tag(EVAL_METRICS)
    slugs = existing.split(",") if existing else []
    slugs.append(slug)
    span.set_tag(EVAL_METRICS, ",".join(slugs))


def record_eval_metric(
    span: t.Optional["Span"],
    *,
    framework: str,
    name: t.Optional[str],
    score: t.Optional[float] = None,
    threshold: t.Optional[float] = None,
    passed: t.Optional[bool] = None,
    status: t.Optional[str] = None,
    model: t.Optional[str] = None,
    cost: t.Optional[float] = None,
    input_tokens: t.Optional[int] = None,
    output_tokens: t.Optional[int] = None,
    total_tokens: t.Optional[int] = None,
    reason: t.Optional[str] = None,
    error: t.Optional[str] = None,
) -> t.Optional[str]:
    """Record a single evaluation metric onto a Test Optimization test span.

    Writes standardized ``eval.*`` tags/metrics and marks the test as an eval
    test (``test.type=eval``). Numeric fields (score, threshold, cost, tokens)
    are stored as span metrics; labels (status, model, reason, ...) as span
    tags. ``None`` values are omitted. Repeated metric names within the same
    test are de-duplicated.

    Never raises: any failure is logged at debug level so that instrumentation
    can never break the user's test.

    :param span: the test root span (``type=test``); a no-op if ``None``.
    :param framework: the eval framework producing the metric (e.g. ``deepeval``).
    :param name: the metric's display name (e.g. ``"Answer Relevancy"``).
    :return: the (possibly de-duplicated) metric slug written, or ``None``.
    """
    if span is None:
        return None
    try:
        span.set_tag(TestTag.TEST_TYPE, TestType.EVAL)
        _set_tag(span, EVAL_FRAMEWORK, framework)

        slug = _dedupe_slug(span, _slugify(name))
        base = f"{EVAL_NAMESPACE}.{slug}"

        # name is required and always set (also the de-dupe sentinel).
        span.set_tag(f"{base}.name", str(name) if name else "metric")

        if status is None:
            if error:
                status = STATUS_ERROR
            elif passed is not None:
                status = STATUS_PASS if passed else STATUS_FAIL
        _set_tag(span, f"{base}.status", status)

        _set_metric(span, f"{base}.score", score)
        _set_metric(span, f"{base}.threshold", threshold)
        if passed is not None:
            _set_metric(span, f"{base}.passed", 1.0 if passed else 0.0)
        _set_metric(span, f"{base}.cost", cost)
        _set_metric(span, f"{base}.tokens.input", input_tokens)
        _set_metric(span, f"{base}.tokens.output", output_tokens)
        _set_metric(span, f"{base}.tokens.total", total_tokens)

        _set_tag(span, f"{base}.model", model)
        _set_tag(span, f"{base}.reason", reason)
        _set_tag(span, f"{base}.error", error)

        _update_rollups(span, slug)
        return slug
    except Exception:
        log.debug("eval: failed to record eval metric on test span", exc_info=True)
        return None
