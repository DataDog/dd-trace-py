import importlib
import json
import re
import types
from typing import Any
from typing import List
from typing import Optional
from typing import Text
from typing import Union
import zlib

from hypothesis import given
from hypothesis import seed
from hypothesis import settings
from hypothesis.strategies import binary
from hypothesis.strategies import builds
from hypothesis.strategies import one_of
from hypothesis.strategies import text

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._ast.ast_patching import astpatch_module
from ddtrace.appsec._iast._ast.ast_patching import iastpatch
from ddtrace.appsec._iast._ast.ast_patching import initialize_iast_lists
from ddtrace.appsec._iast._iast_request_context import get_iast_reporter
from ddtrace.appsec._iast._iast_request_context_base import end_iast_context
from ddtrace.appsec._iast._iast_request_context_base import set_iast_request_enabled
from ddtrace.appsec._iast._iast_request_context_base import start_iast_context
from ddtrace.appsec._iast.main import patch_iast
from ddtrace.appsec._iast.sampling.vulnerability_detection import _reset_global_limit


# Check if the log contains "iast::" to raise an error if that’s the case BUT, if the logs contains
# "iast::instrumentation::" or "iast::instrumentation::"
# are valid
IAST_VALID_LOG = re.compile(r"^iast::(?!instrumentation::|propagation::context::|propagation::sink_point).*$")


class IastTestException(Exception):
    pass


def get_line(label: Text, filename: Optional[Text] = None):
    """get the line number after the label comment in source file `filename`"""
    with open(filename, "r") as file_in:
        for nb_line, line in enumerate(file_in):
            if re.search("label " + re.escape(label), line):
                return nb_line + 2
    raise AssertionError("label %s not found" % label)


def get_line_and_hash(label: Text, vuln_type: Text, filename=None, fixed_line=None):
    """return the line number and the associated vulnerability hash for `label` and source file `filename`"""

    if fixed_line is not None:
        line = fixed_line
    else:
        line = get_line(label, filename=filename)
    rep = "Vulnerability(type='%s', location=Location(path='%s', line=%s))" % (vuln_type, filename, line)
    hash_value = zlib.crc32(rep.encode())

    return line, hash_value


def _iast_patched_module_and_patched_source(module_name, new_module_object=False):
    module = importlib.import_module(module_name)
    module_path, patched_source = astpatch_module(module)
    assert patched_source is not None
    compiled_code = compile(patched_source, module_path, "exec")
    module_changed = types.ModuleType(module_name) if new_module_object else module
    exec(compiled_code, module_changed.__dict__)
    return module_changed, patched_source


def _iast_patched_module(module_name, new_module_object=False, should_patch_iast=False):
    if should_patch_iast:
        patch_iast()
    initialize_iast_lists()
    res = iastpatch.should_iast_patch(module_name)
    if res >= iastpatch.ALLOWED_USER_ALLOWLIST:
        module, _ = _iast_patched_module_and_patched_source(module_name, new_module_object)
    else:
        raise IastTestException(f"IAST Test Error: module {module_name} was excluded: {res}")
    return module


TEXT_TYPE = Union[str, bytes, bytearray]


class CustomStr(str):
    __slots__ = ()


class CustomBytes(bytes):
    pass


class CustomBytearray(bytearray):
    pass


non_empty_text = text().filter(lambda x: x not in ("",) and not x.startswith("\x00"))
non_empty_binary = binary().filter(lambda x: x not in (b"",) and not x.startswith(b"\x00"))

string_strategies: List[Any] = [
    text(),  # regular str
    binary(),  # regular bytes
    builds(bytearray, binary()),  # regular bytearray
    builds(CustomStr, text()),  # custom str subclass
    builds(CustomBytes, binary()),  # custom bytes subclass
    builds(CustomBytearray, binary()),  # custom bytearray subclass
]

string_valid_to_taint_strategies: List[Any] = [
    non_empty_text,  # regular str
    non_empty_binary,  # regular bytes
    builds(bytearray, non_empty_binary),  # regular bytearray
]


def iast_hypothesis_test(func):
    return seed(42)(settings(max_examples=1000)(given(one_of(*string_strategies))(func)))


def _get_iast_data():
    span_report = get_iast_reporter()
    data = span_report.build_and_scrub_value_parts()
    return data


def _start_iast_context_and_oce(span=None):
    oce.reconfigure()
    request_iast_enabled = False
    if oce.acquire_request(span):
        start_iast_context()
        request_iast_enabled = True

    set_iast_request_enabled(request_iast_enabled)


def _end_iast_context_and_oce(span=None):
    end_iast_context(span)
    oce.release_request()
    _reset_global_limit()


def load_iast_report(span):
    """
    Load the IAST report from the span.
    This is used to ensure that the IAST report is available in the span for testing purposes.
    """
    if not hasattr(span, "get_tag"):
        iast_report_json = span["meta"].get(IAST.JSON)
        if iast_report_json is None:
            iast_report = span.get("meta_struct", {}).get("iast")
        else:
            iast_report = json.loads(iast_report_json)

    else:
        iast_report_json = span.get_tag(IAST.JSON)
        if iast_report_json is None:
            iast_report = span.get_struct_tag(IAST.STRUCT)
        else:
            iast_report = json.loads(iast_report_json)
    return iast_report
