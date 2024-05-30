import os
import random
import subprocess

import requests

from ddtrace.appsec._iast._utils import _is_iast_enabled


if _is_iast_enabled():
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject


def test_doit():
    origin_string1 = "hiroot"

    if _is_iast_enabled():
        tainted_string_2 = taint_pyobject(
            pyobject="1234", source_name="abcdefghijk", source_value="1234", source_origin=OriginType.PARAMETER
        )
    else:
        tainted_string_2 = "1234"

    string1 = str(origin_string1)  # String with 1 propagation range
    string2 = str(tainted_string_2)  # String with 1 propagation range

    string3 = string1 + string2  # 2 propagation ranges: hiroot1234
    string4 = "-".join([string3, string3, string3])  # 6 propagation ranges: hiroot1234-hiroot1234-hiroot1234
    string5 = string4[0:20]  # 1 propagation range: hiroot1234-hiroot123
    string6 = string5.title()  # 1 propagation range: Hiroot1234-Hiroot123
    string7 = string6.upper()  # 1 propagation range: HIROOT1234-HIROOT123
    string8 = "%s_notainted" % string7  # 1 propagation range: HIROOT1234-HIROOT123_notainted
    string9 = "notainted_{}".format(string8)  # 1 propagation range: notainted_HIROOT1234-HIROOT123_notainted
    string10 = "nottainted\n" + string9  # 2 propagation ranges: notainted\nnotainted_HIROOT1234-HIROOT123_notainted
    string11 = string10.splitlines()[1]  # 1 propagation range: notainted_HIROOT1234-HIROOT123_notainted
    string12 = string11 + "_notainted"  # 1 propagation range: notainted_HIROOT1234-HIROOT123_notainted_notainted
    string13 = string12.rsplit("_", 1)[0]  # 1 propagation range: notainted_HIROOT1234-HIROOT123_notainted

    try:
        # Path traversal vulnerability
        m = open("/" + string13 + ".txt")
        _ = m.read()
    except Exception:
        pass

    try:
        # Command Injection vulnerability
        _ = subprocess.Popen("ls " + string9)
    except Exception:
        pass

    try:
        # SSRF vulnerability
        requests.get("http://" + "foobar")
        # urllib3.request("GET", "http://" + "foobar")
    except Exception:
        pass

    # Weak Randomness vulnerability
    _ = random.randint(1, 10)

    # os path propagation
    string14 = os.path.join(string13, "a")  # 1 propagation range: notainted_HIROOT1234-HIROOT123_notainted/a
    string15 = os.path.split(string14)[0]  # 1 propagation range: notainted_HIROOT1234-HIROOT123_notainted
    string16 = os.path.dirname(
        string15 + "/" + "foobar"
    )  # 1 propagation range: notainted_HIROOT1234-HIROOT123_notainted
    string17 = os.path.basename("/foobar/" + string16)  # 1 propagation range: notainted_HIROOT1234-HIROOT123_notainted
    string18 = os.path.splitext(string17 + ".jpg")[0]  # 1 propagation range: notainted_HIROOT1234-HIROOT123_notainted
    string19 = os.path.normcase(string18)  # 1 propagation range: notainted_HIROOT1234-HIROOT123_notainted
    string20 = os.path.splitdrive(string19)[1]  # 1 propagation range: notainted_HIROOT1234-HIROOT123_notainted

    expected = "notainted_HIROOT1234-HIROOT123_notainted"  # noqa: F841
    # assert string20 == expected
    return string20
