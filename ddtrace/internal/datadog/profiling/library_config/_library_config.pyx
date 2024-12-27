# distutils: language = c++
# cython: language_level=3
# Right now, this file lives in the profiling-internal directory even though the interface itself is not specific to
# profiling. This is because the config code is bundled in the libdatadog Profiling FFI, which saves a
# considerable amount of binary size, # and it's cumbersome to set an RPATH on that dependency from a different location

import os

from libcpp.vector cimport vector
from libcpp.string cimport string

from .._types import StringType
from ..util import ensure_binary_or_empty
from typing import Dict

cdef extern from "<string_view>" namespace "std" nogil:
    cdef cppclass string_view:
        string_view(const char* s, size_t count)

# For now, the config code is bundled in the libdatadog Profiling FFI.
# This is primarily to reduce binary size.
cdef extern from "library_config_interface.hpp" nogil:
    void libraryconfig_set_envp(vector[string_view] envp)
    void libraryconfig_set_args(vector[string_view] args)
    ConfigVec libraryconfig_generate_config(bint debug_logs)

cdef extern from "library_config.hpp" namespace "Datadog" nogil:
    cdef struct ConfigEntry:
        string key
        string value
    cdef struct ConfigVec:
        bint len
        ConfigEntry *ptr


def set_envp(envp: list[str]):
    cdef vector[string_view] cpp_envp
    envp_bytes = [ensure_binary_or_empty(env) for env in envp]
    for env in envp_bytes:
        cpp_envp.push_back(string_view(<const char*>env, len(env)))
    libraryconfig_set_envp(cpp_envp)

def set_args(args: list[str]):
    cdef vector[string_view] cpp_args
    args_bytes = [ensure_binary_or_empty(arg) for arg in args]
    for arg in args_bytes:
        cpp_args.push_back(string_view(<const char*>arg, len(arg)))
    libraryconfig_set_args(cpp_args)

def get_config(debug_logs: bool) -> Dict[StringType, StringType]:
    cdef ConfigVec cpp_config = libraryconfig_generate_config(debug_logs)
    result = {}
    for i in range(cpp_config.len):
        config_entry = cpp_config.ptr[i]
        result[config_entry.key.decode('utf-8')] = config_entry.value.decode('utf-8')
    return result
