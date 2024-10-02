from datetime import datetime
import os
import random
import re
import subprocess
from typing import Optional
from typing import Tuple

import anyio
from pydantic import BaseModel
from pydantic import Field
from pydantic_core import SchemaValidator
import requests

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking import taint_pyobject


v = SchemaValidator(
    {
        "type": "typed-dict",
        "fields": {
            "name": {
                "type": "typed-dict-field",
                "schema": {
                    "type": "str",
                },
            },
            "age": {
                "type": "typed-dict-field",
                "schema": {
                    "type": "int",
                    "ge": 18,
                },
            },
            "is_developer": {
                "type": "typed-dict-field",
                "schema": {
                    "type": "default",
                    "schema": {"type": "bool"},
                    "default": True,
                },
            },
        },
    }
)


class AspectModel(BaseModel):
    foo: str = "bar"
    apple: int = 1


class Aspectvalidation(BaseModel):
    timestamp: datetime
    tuple_strings: Tuple[str, str]
    dictionary_strs: dict[str, str]
    tag: Optional[str] = None
    author: Optional[str] = None
    favorited: Optional[str] = None
    limit: int = Field(20, ge=1)
    offset: int = Field(0, ge=0)


def modulo_exceptions(string8_4):
    # Validate we're not leaking in modulo exceptions
    try:
        string8_5 = "notainted_%s_" % (string8_4, string8_4)  # noqa: F841, F507
    except TypeError:
        pass

    try:
        string8_5 = "notainted_%d" % string8_4  # noqa: F841
    except TypeError:
        pass

    try:
        string8_5 = "notainted_%s %s" % "abc", string8_4  # noqa: F841
    except TypeError:
        pass

    try:
        string8_5 = "notainted_%s %s" % "abc", string8_4  # noqa: F841
    except TypeError:
        pass

    try:
        string8_5 = "notainted_%s" % (string8_4)  # noqa: F841
    except TypeError:
        pass

    try:
        string8_5 = "notainted_%s %(name)s" % string8_4, {"name": string8_4}  # noqa: F841, F506
    except TypeError:
        pass

    try:
        string8_5 = "notainted_%(age)d" % {"age": string8_4}  # noqa: F841
    except TypeError:
        pass


def pydantic_object(tag, string_tainted):
    m = Aspectvalidation(
        timestamp="2020-01-02T03:04:05Z",
        tuple_strings=[string_tainted, string_tainted],
        dictionary_strs={
            "wine": string_tainted,
            b"cheese": string_tainted,
            "cabbage": string_tainted,
        },
        tag=string_tainted,
        author=string_tainted,
        favorited=string_tainted,
        limit=20,
        offset=0,
    )

    r1 = v.validate_python({"name": m.tuple_strings[0], "age": 35})

    assert is_pyobject_tainted(m.tuple_strings[0])
    assert is_pyobject_tainted(m.tuple_strings[1])
    assert is_pyobject_tainted(m.dictionary_strs["cabbage"])

    r2 = v.validate_json('{"name": "' + m.tuple_strings[0] + '", "age": 35}')

    assert r1 == {"name": m.tuple_strings[0], "age": 35, "is_developer": True}
    assert r1 == r2
    return m


def sink_points(string_tainted):
    try:
        # Path traversal vulnerability
        m = open("/" + string_tainted + ".txt")
        _ = m.read()
    except Exception:
        pass

    try:
        # Command Injection vulnerability
        _ = subprocess.Popen("ls " + string_tainted)
    except Exception:
        pass

    try:
        # SSRF vulnerability
        requests.get("http://" + string_tainted)
        # urllib3.request("GET", "http://" + "foobar")
    except Exception:
        pass

    # Weak Randomness vulnerability
    _ = random.randint(1, 10)


async def test_doit():
    origin_string1 = "hiroot"
    tainted_string_2 = taint_pyobject(
        pyobject="1234", source_name="abcdefghijk", source_value="1234", source_origin=OriginType.PARAMETER
    )

    string1 = str(origin_string1)  # String with 1 propagation range
    string2 = str(tainted_string_2)  # String with 1 propagation range

    string3 = string1 + string2  # 2 propagation ranges: hiroot1234
    string4 = "-".join([string3, string3, string3])  # 6 propagation ranges: hiroot1234-hiroot1234-hiroot1234
    string5 = string4[0:20]  # 1 propagation range: hiroot1234-hiroot123
    string6 = string5.title()  # 1 propagation range: Hiroot1234-Hiroot123
    string7 = string6.upper()  # 1 propagation range: HIROOT1234-HIROOT123
    string8 = "%s_notainted" % string7
    string8_2 = "%s_%s_notainted" % (string8, string8)
    string8_3 = "notainted_%s_" + string8_2
    string8_4 = string8_3 % "notainted"
    await anyio.to_thread.run_sync(modulo_exceptions, string8_4)

    string9 = "notainted#{}".format(string8_4)
    string9_2 = f"{string9}_notainted"
    string9_3 = f"{string9_2:=^30}_notainted"
    string10 = "nottainted\n" + string9_3
    string11 = string10.splitlines()[1]
    string12 = string11 + "_notainted"
    string13 = string12.rsplit("_", 1)[0]
    string13_2 = string13 + " " + string13
    try:
        string13_3, string13_5, string13_5 = string13_2.split(" ")
    except ValueError:
        pass

    sink_points(string13_2)

    # os path propagation
    string14 = os.path.join(string13_2, "a")
    string15 = os.path.split(string14)[0]
    string16 = os.path.dirname(string15 + "/" + "foobar")
    string17 = os.path.basename("/foobar/" + string16)
    string18 = os.path.splitext(string17 + ".jpg")[0]
    string19 = os.path.normcase(string18)
    string20 = os.path.splitdrive(string19)[1]

    re_slash = re.compile(r"[_.][a-zA-Z]*")
    string21 = re_slash.findall(string20)[0]  # 1 propagation: '_HIROOT

    re_match = re.compile(r"(\w+)", re.IGNORECASE)
    re_match_result = re_match.match(string21)  # 1 propagation: 'HIROOT

    string22_1 = re_match_result[0]  # 1 propagation: '_HIROOT
    string22_2 = re_match_result.groups()[0]  # 1 propagation: '_HIROOT
    string22 = string22_1 + string22_2  # 1 propagation: _HIROOT_HIROOT
    tmp_str = "DDDD"
    string23 = tmp_str + string22  # 1 propagation: 'DDDD_HIROOT_HIROOT

    re_match = re.compile(r"(\w+)(_+)(\w+)", re.IGNORECASE)
    re_match_result = re_match.search(string23)
    string24 = re_match_result.expand(r"DDD_\3")  # 1 propagation: 'DDD_HIROOT

    re_split = re.compile(r"[_.][a-zA-Z]*", re.IGNORECASE)
    re_split_result = re_split.split(string24)

    # TODO(avara1986): DDDD_ is constant but we're tainting all re results
    string25 = re_split_result[0] + " EEE"
    string26 = re.sub(r" EEE", "_OOO", string25, re.IGNORECASE)
    string27 = re.subn(r"OOO", "III", string26, re.IGNORECASE)[0]

    tmp_str2 = "_extend"
    string27 += tmp_str2

    # TODO(avara1986): Pydantic is in the deny list, remove from it and uncomment this lines
    # result = await anyio.to_thread.run_sync(functools.partial(pydantic_object, string_tainted=string27), string27)
    # result = pydantic_object(tag="test2", string_tainted=string27)
    # return result.tuple_strings[0]
    return string27
