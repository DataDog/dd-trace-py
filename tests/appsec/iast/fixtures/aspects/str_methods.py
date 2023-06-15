from collections import namedtuple
import functools
from http.client import HTTPConnection
from http.server import HTTPServer as HTTPServer
from http.server import SimpleHTTPRequestHandler
import json
import operator
import os
import random
import re
import sys
import threading
from typing import TYPE_CHECKING

from six import StringIO


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import Generator
    from typing import Iterable
    from typing import List
    from typing import Optional
    from typing import Sequence
    from typing import Text
    from typing import Tuple


def methodcaller(*args, **kwargs):
    return "im methodcaller"


class WebServerHandler(SimpleHTTPRequestHandler):
    def do_GET(self):  # type: () -> None
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write("OK".encode(encoding="utf_8"))
        return


class StoppableHTTPServer(HTTPServer):
    def run(self):  # type: () -> None
        try:
            self.serve_forever()
        finally:
            # Clean-up server (close socket, etc.)
            self.server_close()


def do_operator_add_params(a, b):
    return a + b


def uppercase_decorator(function):  # type: (Callable) -> Callable
    def wrapper(a, b):  # type: (str, str) -> str
        func = function(a, b)
        return func.upper()

    return wrapper


@uppercase_decorator
def do_add_and_uppercase(a, b):  # type: (str, str) -> str
    return a + b


def _get_dict_value(key):
    META = {"QUERY_STRING": "123", "PARAM1": "456"}
    return META.get(key, "")


def get_full_path(path, force_append_slash=False):
    return "%s%s%s" % (
        path,
        "/" if force_append_slash and not path.endswith("/") else "",
        ("?" + _get_dict_value("QUERY_STRING")) if _get_dict_value("QUERY_STRING") else "",
    )


def get_full_path_methods(path, META, force_append_slash=False):
    return "%s%s%s" % (
        path,
        "/" if force_append_slash and not path.endswith("/") else "",
        ("?" + META.get("QUERY_STRING", "")) if META.get("QUERY_STRING", "") else "",
    )


def do_upper_not_str(s):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def upper(string):  # type: (str) -> str
            return "output"

    my_str = MyStr()
    return my_str.upper(s)


def do_lower_not_str(s):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def lower(string):  # type: (str) -> str
            return "output"

    my_str = MyStr()
    return my_str.lower(s)


def do_swapcase(s):  # type: (str) -> str
    return s.swapcase()


def do_swapcase_not_str(s):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def swapcase(string):  # type: (str) -> str
            return "output"

    my_str = MyStr()
    return my_str.swapcase(s)


def do_title(s):  # type: (str) -> str
    return s.title()


def do_title_not_str(s):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def title(string):  # type: (str) -> str
            return "output"

    my_str = MyStr()
    return my_str.title(s)


def do_capitalize(s):  # type: (str) -> str
    return s.capitalize()


def do_capitalize_not_str(s):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def capitalize(string):  # type: (str) -> str
            return "output"

    my_str = MyStr()
    return my_str.capitalize(s)


def do_decode(s, encoding="utf-8", errors="strict"):  # type: (bytes, str, str) -> str
    return s.decode(encoding, errors)


def do_translate(s, translate_dict):
    # type: (str, dict) -> str
    return s.translate(translate_dict)


def do_decode_simple(s):
    # type: (bytes, str, str) -> str
    return s.decode()


def do_encode(s, encoding="utf-8", errors="strict"):
    # type: (str, str, str) -> bytes
    return s.encode(encoding, errors)


def do_encode_from_dict(s, encoding="utf-8", errors="strict"):
    # type: (str, str, str) -> bytes
    my_dictionary = {}
    my_dictionary["test"] = s
    return my_dictionary.get("test", "").encode(encoding, errors)


def do_str_to_bytes(s):  # type: (str) -> bytes
    if sys.version_info[0] >= 3:
        return bytes(s, encoding="utf-8")
    return bytes(s)


def do_str_to_bytearray(s):  # type: (str) -> bytearray
    if sys.version_info[0] >= 3:
        return bytearray(s, encoding="utf-8")
    return bytearray(s)


def do_str_to_bytes_to_bytearray(s):  # type: (str) -> bytearray
    if sys.version_info[0] >= 3:
        return bytearray(bytes(s, encoding="utf-8"))
    return bytearray(bytes(s))


def do_str_to_bytes_to_bytearray_to_str(s):  # type: (str) -> str
    if sys.version_info[0] >= 3:
        return str(bytearray(bytes(s, encoding="utf-8")), encoding="utf-8")
    return str(bytearray(bytes(s)))


def do_bytearray_to_bytes(s):  # type: (bytearray) -> bytes
    return bytes(s)


def do_bytearray_append(ba):  # type: (bytearray) -> bytes
    ba.append(37)
    return ba


def do_bytearray_extend(ba, b):  # type: (bytearray, bytearray) -> None
    ba.extend(b)
    return ba


def do_bytes_to_str(b):  # type: (bytes) -> str
    if sys.version_info[0] >= 3:
        return str(b, encoding="utf-8")
    return str(b)


def do_bytearray_to_str(b):  # type: (bytearray) -> str
    if sys.version_info[0] >= 3:
        return str(b, encoding="utf-8")
    return str(b)


def do_bytes_to_bytearray(s):  # type: (bytes) -> bytearray
    return bytearray(s)


def do_bytes_to_iter_bytearray(b):  # type: (bytes) -> bytearray
    groups = iter(b.split(b"%"))
    result = bytearray(next(groups, b""))
    return result


def do_encode_not_str(s):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def encode(string):  # type: (str) -> str
            return "output"

    my_str = MyStr()
    return my_str.encode(s)


def do_expandtabs(s):  # type: (str) -> str
    return s.expandtabs()


def do_expandtabs_not_str(s):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def expandtabs(string):  # type: (str) -> str
            return "output"

    my_str = MyStr()
    return my_str.expandtabs(s)


def do_casefold(s):  # type: (str) -> str
    return s.casefold()


def do_casefold_not_str(s):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def casefold(string):  # type: (str) -> str
            return "output"

    my_str = MyStr()
    return my_str.casefold(s)


def do_center(c, i):  # type: (str, int) -> str
    return c.center(i)


def do_center_not_str(c):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def center(string):  # type: (str) -> str
            return "output"

    my_str = MyStr()
    return my_str.center(c)


def do_ljust_not_str(c):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def ljust(string1, string2):  # type: (str, str) -> str
            return "output"

    my_str = MyStr()
    return my_str.ljust(c, c)


def do_ljust(s, width):  # type: (str, int) -> str
    return s.ljust(width)


def do_ljust_2(s, width, fill_char):  # type: (str, int, str) -> str
    return s.ljust(width, fill_char)


def do_lstrip_not_str(c):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def lstrip(string1, string2):  # type: (str, str) -> str
            return "output"

    my_str = MyStr()
    return my_str.lstrip(c, c)


def do_lstrip(s):  # type: (str) -> str
    return s.lstrip()


def do_rstrip_not_str(c):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def rstrip(string1, string2):  # type: (str, str) -> str
            return "output"

    my_str = MyStr()
    return my_str.rstrip(c, c)


def do_split_not_str(c):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def split(string1, string2, string3):  # type: (str, str, str) -> str
            return "output"

    my_str = MyStr()
    return my_str.split(c, c, c)


def do_rsplit_not_str(c):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def rsplit(string1, string2, string3):  # type: (str, str, str) -> str
            return "output"

    my_str = MyStr()
    return my_str.rsplit(c, c, c)


def do_splitlines_not_str(c):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def splitlines(string1, string2, string3):  # type: (str, str, str) -> str
            return "output"

    my_str = MyStr()
    return my_str.splitlines(c, c, c)


def do_partition_not_str(c):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def partition(string1, string2, string3):  # type: (str, str, str) -> str
            return "output"

    my_str = MyStr()
    return my_str.partition(c, c, c)


def do_rpartition_not_str(c):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def rpartition(string1, string2, string3):  # type: (str, str, str) -> str
            return "output"

    my_str = MyStr()
    return my_str.rpartition(c, c, c)


def do_replace_not_str(c):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def replace(string1):  # type: (str) -> str
            return "output"

    my_str = MyStr()
    return my_str.replace(c)


def do_format_not_str(c):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def format(string1):  # type: (str) -> str
            return "output"

    my_str = MyStr()
    return my_str.format(c)


def do_format_map_not_str(c):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def format_map(string1):  # type: (str) -> str
            return "output"

    my_str = MyStr()
    return my_str.format_map(c)


def do_zfill_not_str(c):  # type: (str) -> str
    class MyStr(object):
        @staticmethod
        def zfill(string1):  # type: (str) -> str
            return "output"

    my_str = MyStr()
    return my_str.zfill(c)


path2 = "segundo_path"


def get_full_path_simple(path, force_append_slash=False):
    return "%s%s" % (path, "/" if force_append_slash and path2 else "")


def get_full_path_two_ifs(path, force_append_slash=False):
    return "%s%s" % ("/" if force_append_slash else "", ("?" + path) if force_append_slash else "")


def django_check(all_issues, display_num_errors=False):
    """
    Uses the system check framework to validate entire Django project.
    Raises CommandError for any serious message (error or critical errors).
    If there are only light messages (like warnings), they are printed to
    stderr and no exception is raised.
    """
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50

    header, body, footer = "", "", ""
    visible_issue_count = 0  # excludes silenced warnings
    debugs = [e for e in all_issues if e.level < INFO and not e.is_silenced()]
    infos = [e for e in all_issues if INFO <= e.level < WARNING and not e.is_silenced()]
    warnings = [e for e in all_issues if WARNING <= e.level < ERROR and not e.is_silenced()]
    errors = [e for e in all_issues if ERROR <= e.level < CRITICAL and not e.is_silenced()]
    criticals = [e for e in all_issues if CRITICAL <= e.level and not e.is_silenced()]
    sorted_issues = [
        (criticals, "CRITICALS"),
        (errors, "ERRORS"),
        (warnings, "WARNINGS"),
        (infos, "INFOS"),
        (debugs, "DEBUGS"),
    ]

    for issues, group_name in sorted_issues:
        if issues:
            visible_issue_count += len(issues)
            formatted = (str(e) if e.is_serious() else str(e) for e in issues)
            formatted = "\n".join(sorted(formatted))
            body += "\n%s:\n%s\n" % (group_name, formatted)

    if visible_issue_count:
        header = "System check identified some issues:\n"

    if display_num_errors:
        if visible_issue_count:
            footer += "\n"
        footer += "System check identified %s (%s silenced)." % (
            "no issues"
            if visible_issue_count == 0
            else "1 issue"
            if visible_issue_count == 1
            else "%s issues" % visible_issue_count,
            len(all_issues) - visible_issue_count,
        )

    msg = header + body + footer

    if msg:
        if visible_issue_count:
            return msg, lambda x: x
        else:
            return msg


def django_check_simple(all_issues):
    INFO = 20
    return [e for e in all_issues if e.level < INFO and not e.is_silenced()]


def django_check_simple_formatted(f):
    visible_issue_count = 1
    f += "a %s" % ("b" if visible_issue_count == 0 else "c")
    return f


def django_check_simple_formatted_ifs(f):
    visible_issue_count = 1
    f += "System check identified %s (%s silenced)." % (
        "no issues" if visible_issue_count == 0 else "%s issues" % visible_issue_count,
        5 - visible_issue_count,
    )
    return f


def django_check_simple_formatted_multiple_ifs(f):
    visible_issue_count = 1
    f += "System check identified %s (%s silenced)." % (
        "no issues"
        if visible_issue_count == 0
        else "1 issue"
        if visible_issue_count == 1
        else "%s issues" % visible_issue_count,
        5 - visible_issue_count,
    )
    return f


def django_check_simple_join_function(f):
    f += "-".join(sorted(f))
    return f


def get_abs_path_with_join(s, paths):
    absolute_path = os.path.abspath(os.path.join(s, *paths))
    return absolute_path


def get_abs_path(s):
    absolute_path = os.path.abspath(s)
    return absolute_path


class AutoIncrementClass:
    creation_counter = 0

    def __init__(self):
        self.creation_counter = AutoIncrementClass.creation_counter
        AutoIncrementClass.creation_counter += 1


STATS = {"testing": -1}
STATS_SUB = {2: {"testing": -1}}


class Dummyclass:
    creation_counter = 0

    def __init__(self):
        self.creation_counter = Dummyclass.creation_counter
        Dummyclass.creation_counter += 1


class AutoIncrementWithSubclassClass:
    def __init__(self):
        self.dummy = Dummyclass()
        self.dummy.creation_counter += 1


class SubDummyclass:
    creation_counter = 0

    def __init__(self):
        self.creation_counter = SubDummyclass.creation_counter
        SubDummyclass.creation_counter += 1


class DummyDummyclass:
    def __init__(self):
        self.dummy = SubDummyclass()


class AutoIncrementWithSubSubclassClass:
    def __init__(self):
        self.dummy = DummyDummyclass()
        self.dummy.dummy.creation_counter += 1


def someother_function():  # type: () -> None
    print("Im some other function that should not be replaced")


class NestedEncoderClass0(object):
    base = dict()

    @staticmethod
    def encode(i):
        return str(i)

    @staticmethod
    def timex(j):
        return str(j) + "11111"


class NestedEncoderClass1(object):
    base = NestedEncoderClass0()

    @staticmethod
    def encode(i):
        return str(i)

    @staticmethod
    def timex(j, k):
        return str(j) + "11111" + str(k)


def sensitive_variables(*variables):
    def decorator(func):
        @functools.wraps(func)
        def sensitive_variables_wrapper(*func_args, **func_kwargs):
            sensitive_variables_wrapper.s_variables = variables
            return func(*func_args, **func_kwargs)

        return sensitive_variables_wrapper

    return decorator


@sensitive_variables("parameter")
def do_sensitive_variables(parameter):
    return parameter


def do_return_a_decorator(parameter):
    def do_a_decorator(func):
        do_a_decorator.s_variables = parameter

        def wrapper(*args, **kwargs):
            """A wrapper function"""
            # Extend some capabilities of func
            wrapper.s_variables = parameter
            return func(*args, **kwargs) + " " + parameter

        return wrapper

    return do_a_decorator


@do_return_a_decorator("parameter")
def do_decorated_function():
    """This is docstring for decorated function"""
    return "decorated function"


def do_join_tuple_unpack_with_call_for_mock():  # type: () -> Text
    return os.path.join("UTC", *("A", "B"))


def do_join_tuple_unpack_with_call():  # type: () -> Text
    return os.path.join("UTC", *("A", "B"))


class SampleClass(object):
    TIME_ZONE = "UTC/UTM"

    @staticmethod
    def commonprefix(first, *args):  # type: (Text, List[Any]) -> Sequence
        return os.path.commonprefix(list([first]) + list(args))


def do_join_tuple_unpack_with_call_no_replace():  # type: () -> Sequence
    return SampleClass.commonprefix("/usr/bin", *("/usr/local/lib", "/usr/lib"))


def do_join_tuple_unpack_with_call_with_methods(zoneinfo_root):  # type: (str) -> bool
    simple = SampleClass()
    return os.path.exists(os.path.join(zoneinfo_root, *(simple.TIME_ZONE.split("/"))))


class MapJoin(object):
    @staticmethod
    def join(arg0, foo="a", baz="x"):  # type: (Text, Text, Text) -> Text
        return os.path.join(arg0, foo, baz)


def do_join_map_unpack_with_call():  # type: () -> Text
    return MapJoin.join(arg0="/", **{"foo": "bar", "baz": "qux"})


def no_effect(func):
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


def no_effect_using_wraps(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


def do_upper(s):  # type: (str) -> str
    return s.upper()


def do_lower(s):  # type: (str) -> str
    return s.lower()


class MockIssue:
    def __init__(self, value):  # type: (str) -> None
        self.value = value

    def __repr__(self):  # type: () -> str
        return self.value

    def __str__(self):  # type: () -> str
        return self.value

    @property
    def level(self):  # type: () -> int
        return 1

    def is_silenced(self):  # type: () -> bool
        return False

    def is_serious(self):  # type: () -> bool
        return False


class MyIter:
    position = 0
    _leftover = "my_string"
    _producer = (i for i in ["a", "b", "c"])

    def function_next(self):  # type: () -> str
        """
        Used when the exact number of bytes to read is unimportant.

        This procedure just returns whatever is chunk is conveniently returned
        from the iterator instead. Useful to avoid unnecessary bookkeeping if
        performance is an issue.
        """
        if self._leftover:
            output = self._leftover
            self._leftover = ""
        else:
            output = next(self._producer)
        self.position += len(output)
        return output


def func_iter_sum(a):  # type: (str) -> List
    out = []  # type: List[str]
    out += a, a
    return out


def get_random_string_module_encode(allowed_chars):  # type: (str) -> List[str]
    result = ("%s%s%s" % ("a", "b", "c")).encode("utf-8")
    return [allowed_chars for i in result]


def get_random_string_join(mystring):  # type: (str) -> str
    return "".join(mystring for i in ["1", "2"])


def get_random_string_seed(
    length=12,
    allowed_chars="abcdefghijklmnopqrstuvwxyz" "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
):  # type: (int, str) -> str
    """
    Returns a securely generated random string.

    The default length of 12 with the a-z, A-Z, 0-9 character set returns
    a 71-bit value. log_2((26+26+10)^12) =~ 71 bits
    """
    random.seed(("%s%s%s" % (random.getstate(), "time.time", "settings.SECRET_KEY")).encode("utf-8"))
    return "".join(random.choice(allowed_chars) for i in range(length))


def mark_safe(a):  # type: (str) -> str
    return a


def conditional_escape(a):  # type: (str) -> str
    return a


def format_html(a, args):  # type: (str, *Tuple) -> str
    return a.join(args)


def format_html_join(attrs, args_generator=["a", "b", "c"]):  # type: (str, List[str]) -> str
    result = mark_safe(conditional_escape("/").join(format_html(attrs, tuple(args)) for args in args_generator))
    return result


def get_wrapped_repeat_text_with_join(wrapper):  # type: (Callable) -> Callable
    @wrapper
    def repeat_text_with_join(text, times=2):  # type: (str, int) -> str
        # Use the join to confirm that we use a string-propagation method
        return "_".join([text for i in range(0, times)])

    return repeat_text_with_join


def do_format_with_positional_parameter(
    template,  # type: Text
    parameter,  # type: Any
):  # type: (...) -> Text
    return template.format(parameter)


def do_format_with_named_parameter(
    template,  # type: Text
    value,  # type: Any
):  # type: (...) -> Text
    return template.format(key=value)


def mapper(taint_range):  # type: (Any) -> Text
    return taint_range.origin.parameter_name


def do_args_kwargs_1(format_string, *args_safe, **kwargs_safe):  # type: (str, Any, Any) -> str
    return format_string.format(*args_safe, **kwargs_safe)


def do_args_kwargs_2(format_string, *args_safe, **kwargs_safe):  # type: (str, Any, Any) -> str
    return format_string.format("1", *args_safe, **kwargs_safe)


def do_args_kwargs_3(format_string, *args_safe, **kwargs_safe):  # type: (str, Any, Any) -> str
    return format_string.format("1", "2", *args_safe, **kwargs_safe)


def do_args_kwargs_4(format_string, *args_safe, **kwargs_safe):  # type: (str, Any, Any) -> str
    return format_string.format("1", "2", test_kwarg=3, *args_safe, **kwargs_safe)


def do_format_map(template, mapping):  # type: (str, Dict[str, Any]) -> str
    return template.format_map(mapping)


def do_join(s, iterable):
    # type: (str, Iterable) -> str
    return s.join(iterable)


def do_join_args_kwargs(s, *args, **kwargs):
    # type: (str, Iterable) -> str
    return s.join(*args, **kwargs)


def do_join_tuple(mystring):  # type: (str) -> str
    mystring = mystring
    gen = tuple(mystring + _ for _ in ["1", "2", "3"])
    return "".join(gen)


def do_join_set(mystring):  # type: (str) -> str
    mystring = mystring
    gen = {mystring + _ for _ in ["1", "2", "3"]}
    return "".join(gen)


def do_join_generator(mystring):  # type: (str) -> str
    mystring = mystring
    gen = (mystring for _ in ["1", "2", "3"])
    return "".join(gen)


def do_join_generator_2(mystring):  # type: (str) -> str
    def parts():  # type: () -> Generator
        for i in ["x", "y", "z"]:
            yield i

    return mystring.join(parts())


def do_join_generator_and_title(mystring):  # type: (str) -> str
    mystring = mystring.title()
    gen = (mystring for _ in ["1", "2", "3"])
    return "".join(gen)


def do_modulo(template, parameter):  # type: (Text, Any) -> Text
    return template % parameter


def do_replace(text, old, new, count=-1):  # type: (Text, Text, Text, int) -> Text
    return text.replace(old, new, count)


def do_slice(
    text,  # type: str
    first,  # type: Optional[int]
    second,  # type: Optional[int]
    third,  # type: Optional[int]
):  # type: (...) -> str

    # CAVEAT: the following code is duplicate on purpose (also present in production code),
    # because it needs to expose the slicing in order to be patched correctly.

    cases_key = "{}{}{}".format(
        "0" if first is None else "1",
        "0" if second is None else "1",
        "0" if third is None else "1",
    )
    key_lambda_map = {
        "000": lambda x: x[:],
        "001": lambda x: x[::third],
        "010": lambda x: x[:second],
        "011": lambda x: x[:second:third],
        "100": lambda x: x[first:],
        "101": lambda x: x[first::third],
        "110": lambda x: x[first:second],
        "111": lambda x: x[first:second:third],
    }
    return key_lambda_map[cases_key](text)


def mult_two(a, b):  # type: (Any, Any) -> Any
    return a * b


def inplace_mult(a, b):
    a *= b
    return a


class MyObject(object):
    def __init__(self, str_param):  # type: (str) -> None
        self.str_param = str_param

    def __repr__(self):  # type: () -> str
        return self.str_param + " a"


def do_repr_fstring(a):  # type: (Any) -> str
    return "{:10}".format(a)


def do_slice_2(s, first, second, third):  # type: (str, int, int, int) -> str
    return s[first:second:third]


def do_slice_condition(s, first, second):  # type: (str, int, int) -> str
    return s[first : second or 0]


def do_namedtuple(s):  # type: (str) -> Any
    PathInfo = namedtuple("PathInfo", "name surname")
    my_string = PathInfo(name=s, surname=None)
    return my_string


def do_split_no_args(s):  # type: (str) -> List[str]
    return s.split()


def do_rsplit_no_args(s):  # type: (str) -> List[str]
    return s.rsplit()


def do_split(s, sep, maxsplit=-1):  # type: (str, str, int) -> List[str]
    return s.split(sep, maxsplit)


# foosep is just needed so it has the signature expected by _test_somesplit_impl
def do_splitlines(s, foosep):  # type: (str, str) -> List[str]
    return s.splitlines()


def do_partition(s, sep):  # type: (str, str) -> Tuple[str, str, str]
    return s.partition(sep)


def do_zfill(s, width):  # type: (str, int) -> str
    return s.zfill(width)


def do_rsplit(s, sep, maxsplit=-1):  # type: (str, str, int) -> List[str]
    return s.rsplit(sep, maxsplit)


def do_rstrip_2(s):  # type: (str) -> str
    return s.rstrip()


def do_index(c, i):  # type: (str, int) -> str
    return c[i]


def do_methodcaller(s, func, *args):  # type: (str, str, Any) -> str
    func_method = operator.methodcaller(func, *args)
    return func_method(s)


def get_http_headers(header_key):  # type: (str) -> bytes
    RANDOM_PORT = 0
    server = StoppableHTTPServer(("127.0.0.1", RANDOM_PORT), WebServerHandler)
    thread = threading.Thread(None, server.run)
    thread.start()
    conn = HTTPConnection("127.0.0.1", RANDOM_PORT)
    conn.putrequest("GET", "/", skip_host=True)
    conn.putheader("a", "b", "c")
    server.shutdown()
    thread.join()
    return conn._buffer[2]


def urlunsplit_1(data):  # type: (List[str]) -> str
    scheme, netloc, url, query, fragment = data
    return scheme + "://" + netloc + url + "?" + query + "#" + fragment


def urlunsplit_2(data):  # type: (List[str]) -> str
    netloc, url = data
    url = "//" + (netloc or "")
    return url


def urljoin(bpath, path):  # type: (str, str) -> List[str]
    return bpath.split("/")[:-1] + path.split("/")


class MyMigrationClass:
    def parse_number(self, number):
        return number

    def dict_add(self, app_leaf):
        next_number = (self.parse_number(app_leaf[1]) or 0) + 1
        return next_number


def do_re_sub(orig, replacement, arg_str, *args, **kwargs):
    return re.sub(orig, replacement, arg_str, *args, **kwargs)


def do_re_subn(orig, replacement, arg_str, *args, **kwargs):
    return re.subn(orig, replacement, arg_str, *args, **kwargs)


def do_json_loads(*args, **kwargs):
    return json.loads(*args, **kwargs)


def do_add_re_compile():
    import re

    invalid_unicode_no_surrogate = (
        "[\u0001-\u0008\u000B\u000E-\u001F\u007F-\u009F\uFDD0-\uFDEF"
        "\uFFFE\uFFFF\U0001FFFE\U0001FFFF\U0002FFFE\U0002FFFF"
        "\U0003FFFE\U0003FFFF\U0004FFFE\U0004FFFF\U0005FFFE\U0005FFFF"
        "\U0006FFFE\U0006FFFF\U0007FFFE\U0007FFFF\U0008FFFE\U0008FFFF"
        "\U0009FFFE\U0009FFFF\U000AFFFE\U000AFFFF\U000BFFFE\U000BFFFF"
        "\U000CFFFE\U000CFFFF\U000DFFFE\U000DFFFF\U000EFFFE\U000EFFFF"
        "\U000FFFFE\U000FFFFF\U0010FFFE\U0010FFFF]"
    )  # noqa
    _ = re.compile(invalid_unicode_no_surrogate[:-1] + eval('"\\uD800-\\uDFFF"') + "]")  # pylint:disable=eval-used


def do_stringio_init(string_input):
    return StringIO(string_input)


def do_stringio_init_and_getvalue(string_input):
    xxx = StringIO(string_input)
    return xxx.getvalue()


def do_stringio_init_and_read(string_input):
    xxx = StringIO(string_input)
    return xxx.read()


class NotStringIO:
    def __init__(self, content):
        self.content = content

    def getvalue(self):
        return self.content


def do_stringio_init_param(StringIO, string_input):
    return StringIO(string_input)


def do_stringio_init_and_getvalue_param(StringIO, string_input):
    xxx = StringIO(string_input)
    return xxx.getvalue()
