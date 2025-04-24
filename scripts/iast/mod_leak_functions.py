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
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject


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


class ImSubClassOfAString(str):
    def __add__(self, other):
        return "ImNotAString.__add__!!" + other

    def __iadd__(self, other):
        return "ImNotAString.__iadd__!!" + other


class ImNotAString:
    def __add__(self, other):
        return "ImNotAString.__add__!!" + other

    def __iadd__(self, other):
        return "ImNotAString.__iadd__!!" + other


def add_variants(string_tainted, string_no_tainted) -> str:
    new_string_tainted = string_tainted + string_no_tainted
    im_not_a_string = ImNotAString()
    # TODO(avara1986): it raises seg fault instead of TypeError: can only concatenate str (not "ImNotAString") to str
    # try:
    #     new_string_no_tainted = string_no_tainted + im_not_a_string
    #     assert False
    # except TypeError:
    #     pass
    new_string_no_tainted = im_not_a_string + string_no_tainted
    new_string_no_tainted = ImSubClassOfAString() + new_string_no_tainted + string_no_tainted
    new_string_no_tainted += string_no_tainted
    # TODO(avara1986): it raises seg fault instead of TypeError: can only concatenate str (not "ImNotAString") to str
    # try:
    #     new_string_no_tainted += ImNotAString()
    #     assert False
    # except TypeError:
    #     pass

    im_not_a_string += new_string_no_tainted
    new_string_no_tainted += ImSubClassOfAString()
    new_string_tainted += new_string_no_tainted
    new_string_tainted += new_string_no_tainted
    new_string_tainted += new_string_tainted

    new_bytes_no_tainted = bytes(string_no_tainted, encoding="utf-8") + bytes(string_no_tainted, encoding="utf-8")
    new_bytes_no_tainted += bytes(string_no_tainted, encoding="utf-8")
    new_bytes_no_tainted += bytes(string_no_tainted, encoding="utf-8")
    new_bytes_tainted = bytes(string_tainted, encoding="utf-8") + bytes(string_tainted, encoding="utf-8")
    new_bytes_tainted += bytes(string_no_tainted, encoding="utf-8")
    new_bytes_tainted += bytes(string_tainted, encoding="utf-8")
    new_bytes_tainted += bytes(string_tainted, encoding="utf-8")
    new_bytearray_tainted = bytearray(bytes(string_tainted, encoding="utf-8")) + bytearray(
        bytes(string_tainted, encoding="utf-8")
    )
    new_bytearray_tainted += bytearray(bytes(string_tainted, encoding="utf-8"))
    new_bytearray_tainted += bytearray(bytes(string_no_tainted, encoding="utf-8"))

    new_string_tainted = (
        new_string_tainted
        + new_string_no_tainted
        + str(new_bytes_no_tainted, encoding="utf-8")
        + str(new_bytes_tainted, encoding="utf-8")
        + str(new_bytearray_tainted, encoding="utf-8")
    )
    # print(new_string_tainted)
    return new_string_tainted


def format_variants(string_tainted, string_no_tainted) -> str:
    string_tainted_2 = "My name is {} and I am {} years old.".format(string_tainted, 30)
    string_tainted_3 = "My name is {name} and I am {age} years old.".format(name=string_tainted_2, age=25)
    string_tainted_4 = "{0} is {1} years old. {0} lives in {2}.".format(string_tainted_3, 35, string_no_tainted)
    string_tainted_5 = "|{:<10}|{:^10}|{:>10}|".format(string_tainted_4, string_no_tainted, string_tainted_4)
    string_tainted_6 = "|{:-<10}|{:*^10}|{:.>10}|".format(string_no_tainted, string_tainted_5, string_no_tainted)
    string_tainted_7 = "{} is approximately {:.3f}".format(string_tainted_6, 3.1415926535)
    string_tainted_8 = "The {} is {:,}".format(string_tainted_7, 1000000)
    string_tainted_9 = "{1} Hex: {0:x}, Bin: {0:b}, Oct: {0:o}".format(255, string_tainted_8)
    string_tainted_10 = "{} Success rate: {:.2%}".format(string_tainted_9, 0.8765)
    string_tainted_11 = "{} {:+d}, {:+d}".format(string_tainted_10, 42, -42)
    return string_tainted_11


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


def re_module(string_tainted):
    re_slash = re.compile(r"[_.][a-zA-Z]*")
    string21 = re_slash.findall(string_tainted)[0]  # 1 propagation: '_HIROOT

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

    return string27


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
        requests.get("http://" + string_tainted, timeout=1)
        # urllib3.request("GET", "http://" + "foobar")
    except Exception:
        pass

    _ = eval(f"'a' + '{string_tainted}'")
    # Weak Randomness vulnerability
    _ = random.randint(1, 10)


async def test_doit():
    sample_str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

    origin_string1 = "".join(random.choices(sample_str, k=5))

    string_2 = "".join(random.choices(sample_str, k=20))
    tainted_string_2 = taint_pyobject(
        pyobject=string_2, source_name="abcdefghijk", source_value=string_2, source_origin=OriginType.PARAMETER
    )

    string1 = str(origin_string1)  # String with 1 propagation range
    string2 = str(tainted_string_2)  # String with 1 propagation range

    string3 = add_variants(string2, string1)

    string3_1 = string3[:150]

    string4 = "-".join([string3_1, string3_1, string3_1])
    string4_2 = string1
    string4_2 += " " + " ".join(string_ for string_ in [string4, string4, string4])
    string4_2 += " " + " ".join(string_ for string_ in [string1, string1, string1])

    string4_2 += " " + " ".join([string_ for string_ in [string4_2, string4_2, string4_2]])

    string5 = string4_2[0:100]
    string6 = string5.title()
    string7 = string6.upper()
    string8 = "%s_notainted" % string7
    string8_2 = "%s_%s_notainted" % (string8, string8)
    string8_3 = "notainted_%s_" + string8_2
    string8_4 = string8_3 % "notainted"

    string8_5 = format_variants(string8_4, string1)
    await anyio.to_thread.run_sync(modulo_exceptions, string8_5)

    string8_6 = string8_5[25:150]

    string9 = "notainted#{}".format(string8_6)
    string9_2 = f"{string9}_notainted"
    string9_3 = f"{string9_2:=^30}_notainted"
    string10 = "nottainted\n" + string9_3
    string11 = string10.splitlines()[1]
    string12 = string11 + "_notainted"
    string13 = string12.rsplit("_", 1)[0]
    string13_2 = string13 + " " + string13
    string13_3 = string13_2.strip()
    string13_4 = string13_3.rstrip()
    string13_5 = string13_4.lstrip()
    try:
        string13_5_1, string13_5_2, string13_5_3 = string13_5.split(" ")
    except ValueError:
        pass
    sink_points(string13_5)
    # os path propagation
    string14 = os.path.join(string13_5, "a")
    string15 = os.path.split(string14)[0]
    string16 = os.path.dirname(string15 + "/" + "foobar")
    string17 = os.path.basename("/foobar/" + string16)
    string18 = os.path.splitext(string17 + ".jpg")[0]
    string19 = os.path.normcase(string18)
    string20 = os.path.splitdrive(string19)[1]
    string21 = re_module(string20)
    tmp_str2 = "_extend"
    string21 += tmp_str2

    # TODO(avara1986): pydantic is in the DENY_LIST, remove from it and uncomment this lines
    # result = await anyio.to_thread.run_sync(functools.partial(pydantic_object, string_tainted=string21), string21)
    # result = pydantic_object(tag="test2", string_tainted=string21)
    # return result.tuple_strings[0]
    print(string21)
    return string21
