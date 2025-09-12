from flask import Flask

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._context import create_context
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges


def add_test():
    string_to_taint = "abc"
    create_context()
    result = taint_pyobject(
        pyobject=string_to_taint,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=string_to_taint,
        source_origin=OriginType.PARAMETER,
    )
    string_to_add = "def"
    result_2 = string_to_add + result
    ranges_result = get_tainted_ranges(result_2)
    return ranges_result


def create_app_patch_all():
    app = Flask(__name__)
    return app
