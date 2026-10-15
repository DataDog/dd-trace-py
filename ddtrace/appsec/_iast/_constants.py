from _io import BytesIO
from _io import StringIO
from re import Match
import sys
from typing import Literal

from ddtrace.internal.constants import Constant_Class


TEXT_TYPES = (str, bytes, bytearray)

TAINTEABLE_TYPES = TEXT_TYPES + (Match, BytesIO, StringIO)

# Python 3.14+ template strings support
if sys.version_info >= (3, 14):
    from string.templatelib import Template as TemplateType

    TAINTEABLE_TYPES += (TemplateType,)


TELEMETRY_OFF_NAME = "OFF"
TELEMETRY_DEBUG_NAME = "DEBUG"
TELEMETRY_MANDATORY_NAME = "MANDATORY"
TELEMETRY_INFORMATION_NAME = "INFORMATION"

TELEMETRY_DEBUG_VERBOSITY = 10
TELEMETRY_INFORMATION_VERBOSITY = 20
TELEMETRY_MANDATORY_VERBOSITY = 30
TELEMETRY_OFF_VERBOSITY = 40


class IAST(metaclass=Constant_Class):
    """Specific constants for IAST"""

    JSON: Literal["_dd.iast.json"] = "_dd.iast.json"
    STRUCT: Literal["iast"] = "iast"
    ENABLED: Literal["_dd.iast.enabled"] = "_dd.iast.enabled"
    PATCH_MODULES: Literal["_DD_IAST_PATCH_MODULES"] = "_DD_IAST_PATCH_MODULES"
    ENV_NO_DIR_PATCH: Literal["_DD_IAST_NO_DIR_PATCH"] = "_DD_IAST_NO_DIR_PATCH"
    DENY_MODULES: Literal["_DD_IAST_DENY_MODULES"] = "_DD_IAST_DENY_MODULES"
    SEP_MODULES: Literal[","] = ","
    PATCH_ADDED_SYMBOL_PREFIX: Literal["_ddtrace_"] = "_ddtrace_"

    METRICS_REPORT_LVLS = (
        (TELEMETRY_DEBUG_VERBOSITY, TELEMETRY_DEBUG_NAME),
        (TELEMETRY_INFORMATION_VERBOSITY, TELEMETRY_INFORMATION_NAME),
        (TELEMETRY_MANDATORY_VERBOSITY, TELEMETRY_MANDATORY_NAME),
        (TELEMETRY_OFF_VERBOSITY, TELEMETRY_OFF_NAME),
    )

    TEXT_TYPES = TEXT_TYPES
    TAINTEABLE_TYPES = TAINTEABLE_TYPES
    REQUEST_CONTEXT_KEY: Literal["_iast_env"] = "_iast_env"


class IAST_SPAN_TAGS(metaclass=Constant_Class):
    """Specific constants for IAST span tags"""

    TELEMETRY_REQUEST_TAINTED: Literal["_dd.iast.telemetry.request.tainted"] = "_dd.iast.telemetry.request.tainted"
    TELEMETRY_EXECUTED_SINK: Literal["_dd.iast.telemetry.executed.sink"] = "_dd.iast.telemetry.executed.sink"
    TELEMETRY_SUPPRESSED_VULNERABILITY: Literal["_dd.iast.telemetry.suppressed.vulnerabilities"] = (
        "_dd.iast.telemetry.suppressed.vulnerabilities"
    )
    TELEMETRY_EXECUTED_SOURCE: Literal["_dd.iast.telemetry.executed.source"] = "_dd.iast.telemetry.executed.source"
