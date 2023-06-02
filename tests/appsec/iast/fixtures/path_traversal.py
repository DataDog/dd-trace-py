"""
CAVEAT: the line number is important to some IAST tests, be careful to modify this file and update the tests if you
make some changes
"""
import os

from ddtrace.appsec._constants import IAST
from ddtrace.appsec.iast._input_info import Input_info
from ddtrace.appsec.iast._taint_tracking import taint_pyobject


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def pt_open(origin_string):
    k_string = taint_pyobject(origin_string, Input_info("path", origin_string, IAST.HTTP_REQUEST_PATH))
    m = open(k_string)
    return m.read()
