from typing import Text

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._iast_request_context_base import is_iast_request_enabled
from ddtrace.appsec._iast._logs import iast_error
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._patch_modules import WrapFunctonsForIAST
from ddtrace.appsec._iast._span_metrics import increment_iast_span_metric
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast.constants import VULN_UNTRUSTED_SERIALIZATION
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)


class UntrustedSerialization(VulnerabilityBase):
    vulnerability_type = VULN_UNTRUSTED_SERIALIZATION
    secure_mark = VulnerabilityType.UNTRUSTED_SERIALIZATION


def get_version() -> Text:
    return ""


_IS_PATCHED = False

_MODULES = {
    ("pickle", "load"),  # maps to pickle._load/_pickle.load
    ("pickle", "loads"),  # maps to pickle.loads/_pickle.loads
    ("pickle", "_load"),
    ("pickle", "_loads"),
    ("pickle", "_Unpickler.load"),
    ("_pickle", "load"),
    ("_pickle", "loads"),
    ("_pickle", "Unpickler.load"),
    ("dill", "load"),
    ("dill", "loads"),
    ("yaml", "load"),
    ("yaml", "unsafe_load"),
    ("yaml", "load_all"),
    ("yaml", "unsafe_load_all"),
    ("yaml", "full_load"),
    ("yaml", "full_load_all"),
}


def patch():
    global _IS_PATCHED
    if _IS_PATCHED and not asm_config._iast_is_testing:
        return

    if not asm_config._iast_enabled:
        return

    _IS_PATCHED = True

    iast_funcs = WrapFunctonsForIAST()
    for module, function in _MODULES:
        iast_funcs.wrap_function(
            module,
            function,
            _wrap_serializers,
        )

    iast_funcs.patch()

    _set_metric_iast_instrumented_sink(VULN_UNTRUSTED_SERIALIZATION)


def _wrap_serializers(wrapped, instance, args, kwargs):
    # YAML safe loader handling. If caller uses yaml.load with SafeLoader
    # (either as second positional arg or via Loader kwarg), do not report.
    if not _is_yaml_safe_load(args, kwargs):
        _iast_report_untrusted_serializastion(kwargs.get("data", args[0] if len(args) > 0 else None))
    return wrapped(*args, **kwargs)


def _is_yaml_safe_load(args, kwargs):
    """Return True when a yaml "safe" loader is explicitly provided.

    Detects yaml.load(..., SafeLoader) or yaml.load(..., Loader=SafeLoader) patterns.
    The function imports yaml lazily to avoid hard dependency at import time.
    """
    try:
        import yaml

        loader_kw = kwargs.get("Loader") or kwargs.get("loader")
        loader_pos = args[1] if len(args) > 1 else None
        loader = loader_kw or loader_pos
        return loader is not None and (loader is getattr(yaml, "SafeLoader", object()))
    except Exception:
        # If yaml is not importable or anything fails, do not treat as safe
        return False


def _iast_report_untrusted_serializastion(code_string: Text):
    try:
        if is_iast_request_enabled():
            if (
                isinstance(code_string, IAST.TEXT_TYPES)
                and UntrustedSerialization.has_quota()
                and UntrustedSerialization.is_tainted_pyobject(code_string)
            ):
                UntrustedSerialization.report(evidence_value=code_string)
            # Reports Span Metrics
            increment_iast_span_metric(
                IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, UntrustedSerialization.vulnerability_type
            )
            # Report Telemetry Metrics
            _set_metric_iast_executed_sink(UntrustedSerialization.vulnerability_type)
    except Exception as e:
        iast_error("propagation::sink_point::Error in _iast_report_untrusted_serializastion", e)
