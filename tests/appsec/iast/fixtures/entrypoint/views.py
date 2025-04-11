from flask import Flask


def add_test():
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking._context import create_context
    from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
    from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject

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
    import ddtrace.auto  # noqa: F401

    app = Flask(__name__)
    return app


def create_app_enable_iast_propagation():
    from ddtrace.appsec.iast import enable_iast_propagation

    enable_iast_propagation()
    app = Flask(__name__)
    return app
