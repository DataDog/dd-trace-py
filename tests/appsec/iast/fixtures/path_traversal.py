"""
CAVEAT: the line number is important to some IAST tests, be careful to modify this file and update the tests if you
make some changes
"""
import os

from ddtrace.appsec.iast._taint_tracking import OriginType
from ddtrace.appsec.iast._taint_tracking import taint_pyobject


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def pt_open(origin_string):
    k_string = taint_pyobject(
        origin_string, source_name="path", source_value=origin_string, source_origin=OriginType.PATH
    )
    m = open(k_string)
    return m.read()
