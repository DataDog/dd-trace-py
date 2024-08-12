"""
Script used to generate the config.yaml for the individual aspect benchmarks.
"""

import base64
import inspect
from typing import Any

import yaml

from benchmarks.bm.iast_fixtures import module_functions as mod_unpatched_module_functions
from benchmarks.bm.iast_fixtures import str_methods as mod_unpatched_str_methods
from benchmarks.bm.iast_fixtures import str_methods_py3 as mod_unpatched_str_methods_py3
from benchmarks.bm.utils import override_env


with override_env({"DD_IAST_ENABLED": "True"}):
    from tests.appsec.iast.aspects.conftest import _iast_patched_module

    mod_patched_methods = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")
    mod_patched_methods_py3 = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods_py3")
    mod_patched_module_functions = _iast_patched_module("benchmarks.bm.iast_fixtures.module_functions")


_patching_guide = {
    "str_methods": {
        "original_name": "str_methods",
        "name_patched": "mod_patched_methods",
        "symbol_unpatched": mod_unpatched_str_methods,
    },
    "str_methods_py3": {
        "original_name": "str_methods_py3",
        "name_patched": "mod_patched_methods_py3",
        "symbol_unpatched": mod_unpatched_str_methods_py3,
    },
    "module_functions": {
        "original_name": "module_functions",
        "name_patched": "mod_patched_module_functions",
        "symbol_unpatched": mod_unpatched_module_functions,
    },
}

# excluded = not run in the benchmark (utils, decorators, functions that do more than one aspect and internal
# functions).
# special_arguments = use other arguments than the default "  fOo\t  \n", and "  bAr\t  \n"
_module_meta = {
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
        "do_stringio_init_and_getvalue_param",
        "do_stringio_init_param",
        "wrapper",
        # Since this is exported and passed as a command line argument, bytearray() and byte arguments are not
        # processed well
        "do_bytearray_append",
        "do_bytearray_extend",
        "do_bytearray_to_bytes",
        "do_bytearray_to_str",
        "do_bytes",
        "do_bytes_to_bytearray",
        "do_bytes_to_iter_bytearray",
        "do_bytes_to_str",
        "do_decode",
        "do_decode_simple",
        # Error checking for not string arguments
        "do_capitalize_not_str",
        "do_casefold_not_str",
        "do_center_not_str",
        "do_encode_not_str",
        "do_expandtabs_not_str",
        "do_format_map_not_str",
        "do_format_not_str",
        "do_ljust_not_str",
        "do_lower_not_str",
        "do_lstrip_not_str",
        "do_os_path_splitroot",
        "do_partition_not_str",
        "do_replace_not_str",
        "do_rpartition_not_str",
        "do_rsplit_not_str",
        "do_rstrip_not_str",
        "do_split_not_str",
        "do_splitlines_not_str",
        "do_swapcase_not_str",
        "do_title_not_str",
        "do_upper_not_str",
        "do_zfill_not_str",
        # Variants or combinations of existing ones
        "do_customspec_cstr",
        "do_customspec_formatspec",
        "do_customspec_repr",
        "do_customspec_simple",
        "do_encode_from_dict",
        "do_format",
        "do_format_fill",
        "do_format_with_named_parameter",
        "do_format_with_positional_parameter",
        "do_join_args_kwargs",
        "do_join_generator",
        "do_join_generator_2",
        "do_join_generator_and_title",
        "do_join_set",
        "do_join_tuple",
        "do_ljust_2",
        "do_replit_maxsplit",
        "do_repr_fstring_twice",
        "do_repr_fstring_twice_different_objects",
        "do_repr_fstring_with_expression2",
        "do_repr_fstring_with_expression3",
        "do_repr_fstring_with_expression4",
        "do_repr_fstring_with_expression5",
        "do_repr_fstring_with_format_twice",
        "do_rsplit",
        "do_rsplit_maxsplit",
        "do_rsplit_no_args",
        "do_rsplit_separator",
        "do_rstrip_2",
        "do_slice_2",
        "do_slice_condition",
        "do_slice_negative",
        "do_split",
        "do_split_maxsplit",
        "do_split_no_args",
        "do_split_separator",
        "do_splitlines_no_arg",
        "do_str_to_bytes_to_bytearray",
        "do_str_to_bytes_to_bytearray_to_str",
        "do_stringio_init",
        "do_stringio_init_and_getvalue",
        "do_multiple_string_assignment",
        "do_re_subn",
        "do_rsplit_separator_and_maxsplit",
        "do_str_to_bytearray",
        "do_str_to_bytes",
        "do_repr_fstring_with_expression1",
        "do_repr_fstring_with_format",
        "do_zero_padding_fstring",
    },
    "special_arguments": {
        "do_center": ["foobar", 2],
        "do_encode": ["foobar", "utf-8", "strict"],
        "do_encode_from_dict": ["foobar", "utf-8", "strict"],
        "do_format": ["foobar{}", "baz"],
        "do_format_map": ["foobar{baz}", {"baz": "bar"}],
        "do_format_with_named_parameter": ["foobar{key}", "baz"],
        "do_format_with_positional_parameter": ["foobar{}", "baz"],
        "do_index": ["foobar", 3],
        "do_join": ["foobar", ["baz", "pok"]],
        "do_join_args_kwargs": ["foobar", ["baz", "pok"]],
        "do_ljust": ["foobar", 2],
        "do_ljust_2": ["foobar", 2, "x"],
        "do_modulo": ["foobar%s", "baz"],
        "do_partition": ["foobar", "o"],
        "do_re_sub": ["foobar", "o", "a", 1],
        "do_re_subn": ["foobar", "o", "a", 1],
        "do_replace": ["foobar", "o", "a", 1],
        "do_rplit_separator_and_maxsplit": ["foobar", "o", 1],
        "do_rsplit": ["foo bar baz", " ", 1],
        "do_rsplit_maxsplit": ["foo bar baz", 1],
        "do_rsplit_separator": ["foobar", "o"],
        "do_rsplit_separator_and_maxsplit": ["foo bar baz", " ", 1],
        "do_slice": ["foobar", 1, 3, 1],
        "do_slice_2": ["foobar", 1, 3, 1],
        "do_slice_condition": ["foobar", 1, 3],
        "do_split_maxsplit": ["foo bar baz", 1],
        "do_split_separator": ["foobar", "o"],
        "do_split_separator_and_maxsplit": ["foo bar baz", " ", 1],
        "do_splitlines_keepends": ["foo\nbar", False],
        "do_tuple_string_assignment": ["foo"],
        "do_zfill": ["foobar", 10],
        "do_os_path_basename": ["foo/bar"],
        "do_os_path_split": ["foo/bar"],
        "do_os_path_splitdrive": ["foo/bar"],
        "do_os_path_splitext": ["foo/bar.txt"],
    },
}


def binary_constructor(loader, node):
    value = loader.construct_scalar(node)
    return base64.b64decode(value)


def bytearray_constructor(loader, node):
    value = loader.construct_sequence(node)
    return bytearray(value[0], value[1])


yaml.add_constructor("tag:yaml.org,2002:binary", binary_constructor)
yaml.add_constructor("tag:yaml.org,2002:python/object/apply:builtins.bytearray", bytearray_constructor)


def get_var_name(var):
    frame = inspect.currentframe().f_back
    for name, value in frame.f_locals.items():
        if value is var:
            return name


def generate_functions_dict():
    functions: dict[str, dict[str, Any]] = {}
    for _, module_dict in _patching_guide.items():
        symbol_unpatched = module_dict["symbol_unpatched"]
        # get all the functions that are not classes and not imported from other modules

        for f in dir(symbol_unpatched):
            if f.startswith("_"):
                continue
            if not f.startswith("do_"):
                continue
            if f in _module_meta["excluded"]:
                continue

            args = []
            unpatched_func = getattr(symbol_unpatched, f)

            if f in _module_meta["special_arguments"]:
                args = _module_meta["special_arguments"][f]
            else:
                # get the number of arguments that f takes
                num_args = len(inspect.signature(unpatched_func).parameters)
                if num_args:
                    args = ["  fOobaR\t  \n"] * num_args

            exported_args = repr(args)
            functions[f] = {
                "function_name": f,
                "args": exported_args,
                "mod_original_name": module_dict["original_name"],
            }
    return functions


_config_yaml_content = """\
aspect_no_iast_{function_name}: &aspect_no_iast_{function_name}
    iast_enabled: 0
    processes: 10
    loops: 1
    values: 6
    warmups: 1
    mod_original_name: "bm.iast_fixtures.{mod_original_name}"
    function_name: "{function_name}"
    args: {args}

aspect_iast_{function_name}:
    << : *aspect_no_iast_{function_name}
    processes: 10
    loops: 1
    values: 6
    warmups: 1
    iast_enabled: 1
"""


def generate_benchmark_dirs():
    str_result = "# This is autogenerated by the aspects_benchmarks_generate.py script\n"
    functions = generate_functions_dict()

    for function_name, function_dict in functions.items():
        str_result += _config_yaml_content.format(**function_dict) + "\n"

    str_result += "# end content autogenerated by aspects_benchmarks_generate.py"

    return str_result


if __name__ == "__main__":
    print(generate_benchmark_dirs())
