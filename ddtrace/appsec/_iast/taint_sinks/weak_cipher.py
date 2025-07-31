import os
from typing import Any
from typing import Callable
from typing import Set
from typing import Text

from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast.constants import BLOWFISH_DEF
from ddtrace.appsec._iast.constants import DEFAULT_WEAK_CIPHER_ALGORITHMS
from ddtrace.appsec._iast.constants import DES_DEF
from ddtrace.appsec._iast.constants import RC2_DEF
from ddtrace.appsec._iast.constants import RC4_DEF
from ddtrace.appsec._iast.constants import VULN_WEAK_CIPHER_TYPE
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config

from .._metrics import _set_metric_iast_executed_sink
from .._metrics import _set_metric_iast_instrumented_sink
from .._patch_modules import WrapFunctonsForIAST
from .._span_metrics import increment_iast_span_metric
from ._base import VulnerabilityBase


log = get_logger(__name__)


def get_weak_cipher_algorithms() -> Set:
    CONFIGURED_WEAK_CIPHER_ALGORITHMS = None
    DD_IAST_WEAK_CIPHER_ALGORITHMS = os.getenv("DD_IAST_WEAK_CIPHER_ALGORITHMS")
    if DD_IAST_WEAK_CIPHER_ALGORITHMS:
        CONFIGURED_WEAK_CIPHER_ALGORITHMS = set(
            algo.strip() for algo in DD_IAST_WEAK_CIPHER_ALGORITHMS.lower().split(",")
        )
    return CONFIGURED_WEAK_CIPHER_ALGORITHMS or DEFAULT_WEAK_CIPHER_ALGORITHMS


class WeakCipher(VulnerabilityBase):
    vulnerability_type = VULN_WEAK_CIPHER_TYPE


def get_version() -> Text:
    return ""


_IS_PATCHED = False


def patch():
    """Wrap hashing functions.
    Weak hashing algorithms are those that have been proven to be of high risk, or even completely broken,
    and thus are not fit for use.
    """
    global _IS_PATCHED
    if _IS_PATCHED and not asm_config._iast_is_testing:
        return

    if not asm_config._iast_enabled:
        return

    _IS_PATCHED = True

    iast_funcs = WrapFunctonsForIAST()

    weak_cipher_algorithms = get_weak_cipher_algorithms()
    num_instrumented_sinks = 0
    # pycryptodome methods
    if DES_DEF in weak_cipher_algorithms:
        iast_funcs.wrap_function("Crypto.Cipher.DES", "new", wrapped_aux_des_function)
        num_instrumented_sinks += 1
    if BLOWFISH_DEF in weak_cipher_algorithms:
        iast_funcs.wrap_function("Crypto.Cipher.Blowfish", "new", wrapped_aux_blowfish_function)
        num_instrumented_sinks += 1
    if RC2_DEF in weak_cipher_algorithms:
        iast_funcs.wrap_function("Crypto.Cipher.ARC2", "new", wrapped_aux_rc2_function)
        num_instrumented_sinks += 1
    if RC4_DEF in weak_cipher_algorithms:
        iast_funcs.wrap_function("Crypto.Cipher.ARC4", "ARC4Cipher.encrypt", wrapped_rc4_function)
        num_instrumented_sinks += 1

    if weak_cipher_algorithms:
        iast_funcs.wrap_function("Crypto.Cipher._mode_cbc", "CbcMode.encrypt", wrapped_function)
        iast_funcs.wrap_function("Crypto.Cipher._mode_cfb", "CfbMode.encrypt", wrapped_function)
        iast_funcs.wrap_function("Crypto.Cipher._mode_ecb", "EcbMode.encrypt", wrapped_function)
        iast_funcs.wrap_function("Crypto.Cipher._mode_ofb", "OfbMode.encrypt", wrapped_function)

        num_instrumented_sinks += 4

    # cryptography methods
    iast_funcs.wrap_function(
        "cryptography.hazmat.primitives.ciphers", "Cipher.encryptor", wrapped_cryptography_function
    )
    num_instrumented_sinks += 1

    iast_funcs.patch()
    _set_metric_iast_instrumented_sink(VULN_WEAK_CIPHER_TYPE, num_instrumented_sinks)


def wrapped_aux_rc2_function(wrapped, instance, args, kwargs):
    if hasattr(wrapped, "__func__"):
        result = wrapped.__func__(instance, *args, **kwargs)
    else:
        result = wrapped(*args, **kwargs)
    result._dd_weakcipher_algorithm = "RC2"
    return result


def wrapped_aux_des_function(wrapped, instance, args, kwargs):
    if hasattr(wrapped, "__func__"):
        result = wrapped.__func__(instance, *args, **kwargs)
    else:
        result = wrapped(*args, **kwargs)
    result._dd_weakcipher_algorithm = "DES"
    return result


def wrapped_aux_blowfish_function(wrapped, instance, args, kwargs):
    if hasattr(wrapped, "__func__"):
        result = wrapped.__func__(instance, *args, **kwargs)
    else:
        result = wrapped(*args, **kwargs)
    result._dd_weakcipher_algorithm = "Blowfish"
    return result


def wrapped_rc4_function(wrapped: Callable, instance: Any, args: Any, kwargs: Any) -> Any:
    if asm_config.is_iast_request_enabled:
        if WeakCipher.has_quota():
            WeakCipher.report(
                evidence_value="RC4",
            )
        # Reports Span Metrics
        increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, WeakCipher.vulnerability_type)
        # Report Telemetry Metrics
        _set_metric_iast_executed_sink(WeakCipher.vulnerability_type)

    if hasattr(wrapped, "__func__"):
        return wrapped.__func__(instance, *args, **kwargs)
    return wrapped(*args, **kwargs)


def wrapped_function(wrapped: Callable, instance: Any, args: Any, kwargs: Any) -> Any:
    if asm_config.is_iast_request_enabled:
        if hasattr(instance, "_dd_weakcipher_algorithm"):
            if WeakCipher.has_quota():
                evidence = instance._dd_weakcipher_algorithm + "_" + str(instance.__class__.__name__)
                WeakCipher.report(evidence_value=evidence)

            # Reports Span Metrics
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, WeakCipher.vulnerability_type)
            # Report Telemetry Metrics
            _set_metric_iast_executed_sink(WeakCipher.vulnerability_type)

    if hasattr(wrapped, "__func__"):
        return wrapped.__func__(instance, *args, **kwargs)
    return wrapped(*args, **kwargs)


def wrapped_cryptography_function(wrapped: Callable, instance: Any, args: Any, kwargs: Any) -> Any:
    if asm_config.is_iast_request_enabled:
        algorithm_name = instance.algorithm.name.lower()
        if algorithm_name in get_weak_cipher_algorithms():
            if WeakCipher.has_quota():
                WeakCipher.report(
                    evidence_value=algorithm_name,
                )

            # Reports Span Metrics
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, WeakCipher.vulnerability_type)
            # Report Telemetry Metrics
            _set_metric_iast_executed_sink(WeakCipher.vulnerability_type)

    if hasattr(wrapped, "__func__"):
        return wrapped.__func__(instance, *args, **kwargs)
    return wrapped(*args, **kwargs)
