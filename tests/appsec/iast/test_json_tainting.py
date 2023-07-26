import pytest

from ddtrace.appsec.iast.taint_sinks.json_tainting import unpatch_iast
from tests.utils import override_global_config


try:
    from ddtrace.appsec.iast import oce
    from ddtrace.appsec.iast._taint_tracking import OriginType
    from ddtrace.appsec.iast._taint_tracking import create_context
    from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted
    from ddtrace.appsec.iast._taint_tracking import setup as taint_tracking_setup
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject
except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)


def setup():
    create_context()
    taint_tracking_setup(bytes.join, bytearray.join)
    oce._enabled = True


FIXTURES_PATH = "tests/appsec/iast/fixtures/weak_algorithms.py"


# @pytest.mark.parametrize(
#     "mode,cipher_func",
#     [
#         ("MODE_ECB", "DES_EcbMode"),
#         ("MODE_CFB", "DES_CfbMode"),
#         ("MODE_CBC", "DES_CbcMode"),
#         ("MODE_OFB", "DES_OfbMode"),
#     ],
# )
def test_weak_taint_json(iast_span_defaults):
    from json import loads

    with override_global_config(dict(_iast_enabled=True)):
        input_str = taint_pyobject(
            pyobject='"tainted string"',
            source_name="request_body",
            source_value="hello",
            source_origin=OriginType.PARAMETER,
        )
        assert is_pyobject_tainted(input_str)

        res = loads(input_str)

        assert isinstance(res, str)
        assert is_pyobject_tainted(res)


def test_weak_json_unpatch(iast_span_defaults):
    unpatch_iast()
    assert True
