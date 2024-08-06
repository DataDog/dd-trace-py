"""
Script used to generate the micro benchmarks for individual aspects. This will re-generate all the
"appsec_iast_aspect_micro_[aspect_name]" directories and their content. The directories will contain the
benchmark class for each aspect function and the yaml config file.

Note that new aspect directories are not automatically added to git thus when calling the script new ones
will have to be git-added manually.
"""

import inspect
from io import StringIO
import os
from typing import Any

from benchmarks.bm.utils import override_env
from tests.appsec.iast.fixtures.aspects import module_functions as mod_unpatched_module_functions
from tests.appsec.iast.fixtures.aspects import str_methods as mod_unpatched_str_methods
from tests.appsec.iast.fixtures.aspects import str_methods_py3 as mod_unpatched_str_methods_py3


with override_env({"DD_IAST_ENABLED": "True"}):
    from tests.appsec.iast.aspects.conftest import _iast_patched_module

    mod_patched_methods = _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods")
    mod_patched_methods_py3 = _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods_py3")
    mod_patched_module_functions = _iast_patched_module("tests.appsec.iast.fixtures.aspects.module_functions")


_patching_guide = {
    "str_methods": {
        "original_name": "str_methods",
        "name_unpatched": "mod_unpatched_str_methods",
        "name_patched": "mod_patched_methods",
        "symbol_unpatched": mod_unpatched_str_methods,
        "symbol_patched": mod_patched_methods,
    },
    "str_methods_py3": {
        "original_name": "str_methods_py3",
        "name_unpatched": "mod_unpatched_str_methods_py3",
        "name_patched": "mod_patched_methods_py3",
        "symbol_unpatched": mod_unpatched_str_methods_py3,
        "symbol_patched": mod_patched_methods_py3,
    },
    "module_functions": {
        "original_name": "module_functions",
        "name_unpatched": "mod_unpatched_module_functions",
        "name_patched": "mod_patched_module_functions",
        "symbol_unpatched": mod_unpatched_module_functions,
        "symbol_patched": mod_patched_module_functions,
    },
}

# excluded = not run in the benchmark (utils, decorators, functions that do more than one aspect and internal
# functions).
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
            "do_stringio_init_and_getvalue_param",
            "do_stringio_init_param",
            "wrapper",
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
            "do_join_args_kwargs": (
                "foobar",
                ("baz", "pok"),
            ),
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
            "do_splitlines_keepends": ("foo\nbar", False),
            "do_tuple_string_assignment": ("foo",),
            "do_zfill": ("foobar", 10),
        },
    },
    "str_methods_py3.py": {"excluded": {}, "special_arguments": {}},
    "module_functions.py": {
        "excluded": {
            "do_os_path_splitroot",
        },
        "special_arguments": {
            "do_os_path_basename": ("foo/bar",),
            "do_os_path_split": ("foo/bar",),
            "do_os_path_splitdrive": ("foo/bar",),
            "do_os_path_splitext": ("foo/bar.txt",),
        },
    },
}


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
        base_mod_name = os.path.basename(symbol_unpatched.__file__)

        for f in dir(symbol_unpatched):
            if f.startswith("_"):
                continue
            if not f.startswith("do_"):
                continue
            if f in _module_meta[base_mod_name]["excluded"]:
                continue

            args = []
            unpatched_func = getattr(symbol_unpatched, f)

            if f in _module_meta[base_mod_name]["special_arguments"]:
                args = _module_meta[base_mod_name]["special_arguments"][f]
            else:
                # get the number of arguments that f takes
                num_args = len(inspect.signature(unpatched_func).parameters)
                if num_args:
                    args = ["  fOobaR\t  \n"] * num_args

            functions[f] = {
                "function_name": f,
                "args": args,
                "import_patched_name": module_dict["name_patched"],
                "import_unpatched_name": module_dict["name_unpatched"],
                "mod_original_name": module_dict["original_name"],
            }
    return functions


_config_yaml_content = """\
no_iast: &base_variant
iast_enabled: 0

iast_enabled: &iast_enabled
iast_enabled: 1
"""


_benchmark_class_template = """\
# Note: this is autogenerated using the script benchmarks/bm/iast_utils/aspects_benchmarks_generate.py.
# DO NOT EDIT THIS FILE MANUALLY OR IT COULD BE OVERWRITTEN.

from benchmarks import bm
from benchmarks.bm.utils import override_env
from tests.appsec.iast.fixtures.aspects import {mod_original_name} as {import_unpatched_name}


with override_env({{"DD_IAST_ENABLED": "True"}}):
    from tests.appsec.iast.aspects.conftest import _iast_patched_module

    {import_patched_name} = _iast_patched_module("tests.appsec.iast.fixtures.aspects.{mod_original_name}")


class IAST_Aspects_{function_name}(bm.Scenario):
    iast_enabled: bool

    def run(self):
        def _(loops):
            for _ in range(loops):
                if self.iast_enabled:
                    {import_patched_name}.{function_name}(*{args})
                else:
                    {import_unpatched_name}.{function_name}(*{args})

        yield _
"""


def generate_benchmark_dirs():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    benchmarks_dir = os.path.dirname(os.path.dirname(current_dir))

    functions = generate_functions_dict()
    used_dirs = set()

    for function_name, function_dict in functions.items():
        aspect_bm_dir_name = "appsec_iast_aspect_" + function_name
        full_bm_dir_name = os.path.join(benchmarks_dir, aspect_bm_dir_name)

        # Create the micro benchmark directory
        if not os.path.exists(full_bm_dir_name):
            print("Creating new scenario: ", full_bm_dir_name)
            os.makedirs(full_bm_dir_name)
        else:
            print("Updating scenario: ", full_bm_dir_name)

        used_dirs.add(os.path.split(full_bm_dir_name)[1])

        if not os.path.isdir(full_bm_dir_name):
            raise ValueError(f"{full_bm_dir_name} is not a directory")

        # Create the yaml config file
        config_yaml_file = os.path.join(full_bm_dir_name, "config.yaml")
        with open(config_yaml_file, "w") as f:
            f.write(_config_yaml_content)

        # Create the scenario.py file
        benchmark_class_content = _benchmark_class_template.format(**function_dict)
        scenario_file = os.path.join(full_bm_dir_name, "scenario.py")
        with open(scenario_file, "w") as f:
            f.write(benchmark_class_content.replace("'", '"'))

    # Try to find if there are extra dirs that we didn't generate (they should be removed)
    for root, dirs, _ in os.walk(benchmarks_dir):
        for d in dirs:
            if d.startswith("appsec_iast_aspect_") and d not in used_dirs:
                print(f"\nWARNING: directory '{d}' is not used anymore. Probably needs to be removed.")


if __name__ == "__main__":
    generate_benchmark_dirs()
