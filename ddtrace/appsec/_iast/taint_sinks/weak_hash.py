import os
from typing import Any
from typing import Callable
from typing import Set

from ddtrace.settings.asm import config as asm_config

from ..._constants import IAST_SPAN_TAGS
from .._metrics import _set_metric_iast_executed_sink
from .._metrics import _set_metric_iast_instrumented_sink
from .._patch_modules import WrapModulesForIAST
from .._span_metrics import increment_iast_span_metric
from ..constants import DEFAULT_WEAK_HASH_ALGORITHMS
from ..constants import MD5_DEF
from ..constants import SHA1_DEF
from ..constants import VULN_INSECURE_HASHING_TYPE
from ._base import VulnerabilityBase


def get_weak_hash_algorithms() -> Set:
    CONFIGURED_WEAK_HASH_ALGORITHMS = None
    DD_IAST_WEAK_HASH_ALGORITHMS = os.getenv("DD_IAST_WEAK_HASH_ALGORITHMS")
    if DD_IAST_WEAK_HASH_ALGORITHMS:
        CONFIGURED_WEAK_HASH_ALGORITHMS = set(algo.strip() for algo in DD_IAST_WEAK_HASH_ALGORITHMS.lower().split(","))

    return CONFIGURED_WEAK_HASH_ALGORITHMS or DEFAULT_WEAK_HASH_ALGORITHMS


class WeakHash(VulnerabilityBase):
    vulnerability_type = VULN_INSECURE_HASHING_TYPE


def get_version() -> str:
    return ""


_is_patched = False


def patch(testing=False):
    """Wrap hashing functions.
    Weak hashing algorithms are those that have been proven to be of high risk, or even completely broken,
    and thus are not fit for use.
    """
    global _is_patched
    if _is_patched and not testing:
        return

    if not asm_config._iast_enabled:
        return

    warp_modules = WrapModulesForIAST(testing=testing)

    weak_hash_algorithms = get_weak_hash_algorithms()
    num_instrumented_sinks = 0
    warp_modules.add_module("_hashlib", "HASH.digest", wrapped_digest_function)
    warp_modules.add_module("_hashlib", "HASH.hexdigest", wrapped_digest_function)
    num_instrumented_sinks += 2

    if MD5_DEF in weak_hash_algorithms:
        warp_modules.add_module(("_%s" % MD5_DEF), "MD5Type.digest", wrapped_md5_function)
        warp_modules.add_module(("_%s" % MD5_DEF), "MD5Type.hexdigest", wrapped_md5_function)
        num_instrumented_sinks += 2
    if SHA1_DEF in weak_hash_algorithms:
        warp_modules.add_module(("_%s" % SHA1_DEF), "SHA1Type.digest", wrapped_sha1_function)
        warp_modules.add_module(("_%s" % SHA1_DEF), "SHA1Type.hexdigest", wrapped_sha1_function)
        num_instrumented_sinks += 2

    # pycryptodome methods
    if MD5_DEF in weak_hash_algorithms:
        warp_modules.add_module("Crypto.Hash.MD5", "MD5Hash.digest", wrapped_md5_function)
        warp_modules.add_module("Crypto.Hash.MD5", "MD5Hash.hexdigest", wrapped_md5_function)
        num_instrumented_sinks += 2
    if SHA1_DEF in weak_hash_algorithms:
        warp_modules.add_module("Crypto.Hash.SHA1", "SHA1Hash.digest", wrapped_sha1_function)
        warp_modules.add_module("Crypto.Hash.SHA1", "SHA1Hash.hexdigest", wrapped_sha1_function)
        num_instrumented_sinks += 2

    if num_instrumented_sinks > 0:
        _set_metric_iast_instrumented_sink(VULN_INSECURE_HASHING_TYPE, num_instrumented_sinks)


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
