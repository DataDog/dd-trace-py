import os
from typing import Any
from typing import Callable
from typing import Set

from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config

from ..._common_module_patches import try_unwrap
from ..._constants import IAST_SPAN_TAGS
from .._metrics import _set_metric_iast_executed_sink
from .._metrics import _set_metric_iast_instrumented_sink
from .._patch_modules import WrapFunctonsForIAST
from .._span_metrics import increment_iast_span_metric
from ..constants import DEFAULT_WEAK_HASH_ALGORITHMS
from ..constants import MD5_DEF
from ..constants import SHA1_DEF
from ..constants import VULN_INSECURE_HASHING_TYPE
from ._base import VulnerabilityBase
from .utils import patch_once


log = get_logger(__name__)


def get_weak_hash_algorithms() -> Set:
    CONFIGURED_WEAK_HASH_ALGORITHMS = None
    DD_IAST_WEAK_HASH_ALGORITHMS = os.getenv("DD_IAST_WEAK_HASH_ALGORITHMS")
    if DD_IAST_WEAK_HASH_ALGORITHMS:
        CONFIGURED_WEAK_HASH_ALGORITHMS = set(algo.strip() for algo in DD_IAST_WEAK_HASH_ALGORITHMS.lower().split(","))

    log.debug(
        "Configuring DD_IAST_WEAK_HASH_ALGORITHMS env var:%s. Result: %s",
        DD_IAST_WEAK_HASH_ALGORITHMS,
        CONFIGURED_WEAK_HASH_ALGORITHMS,
    )

    return CONFIGURED_WEAK_HASH_ALGORITHMS or DEFAULT_WEAK_HASH_ALGORITHMS


class WeakHash(VulnerabilityBase):
    vulnerability_type = VULN_INSECURE_HASHING_TYPE


def get_version() -> str:
    return ""


def patch():
    """Wrap hashing functions.
    Weak hashing algorithms are those that have been proven to be of high risk, or even completely broken,
    and thus are not fit for use.
    """
    iast_funcs = WrapFunctonsForIAST()

    weak_hash_algorithms = get_weak_hash_algorithms()

    if MD5_DEF in weak_hash_algorithms:
        iast_funcs.wrap_function(("_%s" % MD5_DEF), "MD5Type.digest", wrapped_md5_function)
        iast_funcs.wrap_function(("_%s" % MD5_DEF), "MD5Type.hexdigest", wrapped_md5_function)

    iast_funcs.patch()


def wrapped_digest_function(wrapped: Callable, instance: Any, args: Any, kwargs: Any) -> Any:
    if asm_config.is_iast_request_enabled:
        if WeakHash.has_quota() and instance.name.lower() in get_weak_hash_algorithms():
            WeakHash.report(
                evidence_value=instance.name,
            )

        # Reports Span Metrics
        increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, WeakHash.vulnerability_type)
        # Report Telemetry Metrics
        _set_metric_iast_executed_sink(WeakHash.vulnerability_type)

    if hasattr(wrapped, "__func__"):
        return wrapped.__func__(instance, *args, **kwargs)
    return wrapped(*args, **kwargs)


def wrapped_md5_function(wrapped: Callable, instance: Any, args: Any, kwargs: Any) -> Any:
    return wrapped_function(wrapped, MD5_DEF, instance, args, kwargs)


def wrapped_sha1_function(wrapped: Callable, instance: Any, args: Any, kwargs: Any) -> Any:
    return wrapped_function(wrapped, SHA1_DEF, instance, args, kwargs)


def wrapped_new_function(wrapped: Callable, instance: Any, args: Any, kwargs: Any) -> Any:
    if asm_config.is_iast_request_enabled:
        if WeakHash.has_quota() and args[0].lower() in get_weak_hash_algorithms():
            WeakHash.report(
                evidence_value=args[0].lower(),
            )
        # Reports Span Metrics
        increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, WeakHash.vulnerability_type)
        # Report Telemetry Metrics
        _set_metric_iast_executed_sink(WeakHash.vulnerability_type)

    if hasattr(wrapped, "__func__"):
        return wrapped.__func__(instance, *args, **kwargs)
    return wrapped(*args, **kwargs)


def wrapped_function(wrapped: Callable, evidence: str, instance: Any, args: Any, kwargs: Any) -> Any:
    if asm_config.is_iast_request_enabled:
        if WeakHash.has_quota():
            WeakHash.report(
                evidence_value=evidence,
            )
        # Reports Span Metrics
        increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, WeakHash.vulnerability_type)
        # Report Telemetry Metrics
        _set_metric_iast_executed_sink(WeakHash.vulnerability_type)

    if hasattr(wrapped, "__func__"):
        return wrapped.__func__(instance, *args, **kwargs)
    return wrapped(*args, **kwargs)
