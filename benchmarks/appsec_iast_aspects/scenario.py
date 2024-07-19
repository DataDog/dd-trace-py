import dataclasses
import inspect
from io import StringIO
import os
from typing import Callable, Any

from benchmarks import bm
from benchmarks.bm.utils import override_env

with override_env({"DD_IAST_ENABLED": "True"}):
    from tests.appsec.iast.aspects.conftest import _iast_patched_module
    mod_patched_str_methods = _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods")
    mod_patched_str_methods_py3 = _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods_py3")
    mod_patched_module_functions = _iast_patched_module("tests.appsec.iast.fixtures.aspects.module_functions")

from tests.appsec.iast.fixtures.aspects import str_methods as mod_unpatched_str_methods
from tests.appsec.iast.fixtures.aspects import str_methods_py3 as mod_unpatched_str_methods_py3
from tests.appsec.iast.fixtures.aspects import module_functions as mod_unpatched_module_functions

_unpatched2patched = [
    (mod_unpatched_str_methods, mod_patched_str_methods),
    (mod_unpatched_str_methods_py3, mod_patched_str_methods_py3),
    (mod_unpatched_module_functions, mod_patched_module_functions),
]

# excluded = not run in the benchmark (utils, decorators, functions that do more than one aspect and internal functions).
# special_arguments = use other arguments than the default "  fOo\t  \n", and "  bAr\t  \n"
_module_meta = {
    "str_methods.py": {
        "excluded": {
            "do_args_kwargs_1",
            "do_args_kwargs_2",
            "do_args_kwargs_3",
            "do_args_kwargs_4",
            "do_decorated_function",
            "do_format_key_error",
            "do_join_map_unpack_with_call",
            "do_join_tuple_unpack_with_call",
            "do_join_tuple_unpack_with_call_for_mock",
            "do_join_tuple_unpack_with_call_no_replace",
            "do_join_tuple_unpack_with_call_with_methods",
            "do_json_loads",
            "do_methodcaller",
            "do_return_a_decorator",
            "do_slice_2_and_two_strings",
            "do_slice_complex",
        },
        "special_arguments": {
            "do_bytearray_append": (bytearray(b"foobar"),),
            "do_bytearray_extend": (bytearray(b"foo"), bytearray(b"bar")),
            "do_bytearray_to_bytes": (bytearray(b"foobar"),),
            "do_bytearray_to_str": (bytearray(b"foobar"),),
            "do_bytes": (bytearray(b"foobar"),),
            "do_bytes_to_bytearray": (b"foobar",),
            "do_bytes_to_iter_bytearray": (b"foobar",),
            "do_bytes_to_str": (b"foobar",),
            "do_center": ("foobar", 2),
            "do_decode": (b"foobar",),
            "do_decode_simple": (b"foobar",),
            "do_encode": ("foobar", "utf-8", "strict"),
            "do_encode_from_dict": ("foobar", "utf-8", "strict"),
            "do_format": ("foobar{}", "baz"),
            "do_format_map": ("foobar{baz}", {"baz": "bar"}),
            "do_format_with_named_parameter": ("foobar{key}", "baz"),
            "do_format_with_positional_parameter": ("foobar{}", "baz"),
            "do_index": ("foobar", 3),
            "do_join": ("foobar", ["baz", "pok"]),
            "do_join_args_kwargs": ("foobar", ("baz", "pok"),),
            "do_ljust": ("foobar", 2),
            "do_ljust_2": ("foobar", 2, "x"),
            "do_modulo": ("foobar%s", "baz"),
            "do_partition": ("foobar", "o"),
            "do_re_sub": ("foobar", "o", "a", 1),
            "do_re_subn": ("foobar", "o", "a", 1),
            "do_replace": ("foobar", "o", "a", 1),
            "do_rplit_separator_and_maxsplit": ("foobar", "o", 1),
            "do_rsplit": ("foo bar baz", " ", 1),
            "do_rsplit_maxsplit": ("foo bar baz", 1),
            "do_rsplit_separator": ("foobar", "o"),
            "do_rsplit_separator_and_maxsplit": ("foo bar baz", " ", 1),
            "do_slice": ("foobar", 1, 3, 1),
            "do_slice_2": ("foobar", 1, 3, 1),
            "do_slice_condition": ("foobar", 1, 3),
            "do_split_maxsplit": ("foo bar baz", 1),
            "do_split_separator": ("foobar", "o"),
            "do_split_separator_and_maxsplit": ("foo bar baz", " ", 1),
            "do_split_separator_and_maxsplit": ("foobar", "o", 1),
            "do_splitlines_keepends": ("foo\nbar", False),
            "do_stringio_init_and_getvalue_param": (StringIO, "foobar"),
            "do_stringio_init_param": (StringIO, "foobar"),
            "do_tuple_string_assignment": ("foo",),
            "do_zfill": ("foobar", 10),
        }
    },
    "str_methods_py3.py": {
        "excluded": {},
        "special_arguments": {
        }
    },
    "module_functions.py": {
        "excluded": {
            "do_os_path_splitroot",
        },
        "special_arguments": {
            "do_os_path_basename": ("foo/bar",),
            "do_os_path_split": ("foo/bar",),
            "do_os_path_splitdrive": ("foo/bar",),
            "do_os_path_splitext": ("foo/bar.txt",),
        }
    }
}

functions = {}

_unpatched_functions: list[tuple[Callable, str, list[Any]]] = []
_patched_functions: list[tuple[Callable, str, list[Any]]] = []


for unpatched_mod, patched_mod in _unpatched2patched:
    # get all the functions that are not classes and not imported from other modules
    base_mod_name = os.path.basename(unpatched_mod.__file__)

    for f in dir(unpatched_mod):
        if f.startswith("_"):
            continue
        if not f.startswith("do_"):
            continue
        if f in _module_meta[base_mod_name]["excluded"]:
            continue

        args = []
        unpatched_func = getattr(unpatched_mod, f)
        patched_func = getattr(patched_mod, f)

        if f in _module_meta[base_mod_name]["special_arguments"]:
            args = _module_meta[base_mod_name]["special_arguments"][f]
        else:
            # get the number of arguments that f takes
            num_args = len(inspect.signature(unpatched_func).parameters)
            if num_args:
                args = ["  fOobaR\t  \n"] * num_args

        functions['f'] = {
            'unpatched': unpatched_func,
            'patched': patched_func,
            'args': args
        }


class IAST_Aspects(bm.Scenario):
    iast_enabled: bool

    def run(self):
        def _(loops):
            for _ in range(loops):
                if self.iast_enabled:
                    for f in functions.values():
                        f['patched'](*f['args'])
                else:
                    for f in functions.values():
                        f['unpatched'](*f['args'])
        yield _