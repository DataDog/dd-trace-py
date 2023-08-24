#!/usr/bin/env python3
import string
import os

from random import choice

import time
import psutil
from statistics import median

from ddtrace.appsec.iast._taint_tracking._native.taint_tracking import OriginType
from ddtrace.appsec.iast._taint_tracking._native.taint_tracking import Source
from ddtrace.appsec.iast._taint_tracking._native.taint_tracking import TaintRange
from ddtrace.appsec.iast._taint_tracking._native.taint_tracking import set_ranges
from ddtrace.appsec.iast._taint_tracking._native.taint_tracking import get_ranges
from ddtrace.appsec.iast._taint_tracking import create_context
from ddtrace.appsec.iast._taint_tracking import contexts_reset
from ddtrace.appsec.iast._taint_tracking import num_created_tobjects
from ddtrace.appsec.iast._taint_tracking import num_destroyed_tobjects
from ddtrace.appsec.iast._taint_tracking import num_created_ranges
from ddtrace.appsec.iast._taint_tracking import num_destroyed_ranges
from ddtrace.appsec.iast._taint_tracking import destroy_context
from ddtrace.appsec.iast._taint_tracking import num_contexts
from ddtrace.appsec.iast._taint_tracking import initializer_size
from ddtrace.appsec.iast._taint_tracking import active_map_addreses_size
from ddtrace.appsec.iast._taint_tracking import setup
from ddtrace.appsec.iast._taint_tracking.aspects import join_aspect, add_aspect
from ddtrace.appsec.iast._taint_tracking import num_objects_tainted

from typing import Callable



ENABLE_PROPAGATION = bool(int(os.environ.get("ENABLE_PROPAGATION", True)))
ENABLE_TAINTING = bool(int(os.environ.get("ENABLE_TAINTING", True)))
ENABLE_CHECKING = bool(int(os.environ.get("ENABLE_CHECKING", True)))
DEBUG_MODE = bool(int(os.environ.get("DEBUG_MODE", True)))

TAINT_ORIGIN = Source(name="sample_name", value="sample_value", origin=OriginType.PARAMETER)

CHECK_RANGES = [
    TaintRange(0, 3, TAINT_ORIGIN),
    TaintRange(21, 3, TAINT_ORIGIN),
    TaintRange(41, 3, TAINT_ORIGIN)
]


def get_random_string(length):
    return ''.join(choice(string.ascii_letters) for i in range(length))


def taint_pyobject_with_ranges(pyobject, ranges):  # type: (Any, tuple) -> None
    set_ranges(pyobject, tuple(ranges))


def print_created():
    print("After create context:")
    print("Num created tobjects: %s\n"
          "Num destroyed tobjects: %s\n"
          "Num created ranges: %s\n"
          "Num destroyed ranges: %s\n" %
          (num_created_tobjects(), num_destroyed_tobjects(), num_created_ranges(),
           num_destroyed_ranges()))


def new_request(enable_propagation: bool) -> str:
    tainted = b"my_string".decode("ascii")
    # destroy_context()
    contexts_reset()
    create_context()

    print_created()
    print("#" * 50)

    if ENABLE_TAINTING:
        taint_pyobject_with_ranges(tainted, (CHECK_RANGES[0],))
    return tainted


def normal_function(internal_loop: int, tainted: str) -> str:
    # FIXME: we're assigning res to value in each iteration to inhibit an inplace concat
    #        optimization (INPLACE_ADD) in the base case. Ideally, we should implement an aspect
    #        for INPLACE_ADD assuming we can optimize something for that case.
    value = ''
    res = value
    for j in range(internal_loop):
        # FIXME: we're using replace instead of join in other languages.
        res += ("_".join((tainted, "_", tainted)))
        value = res
        res += tainted
        value = res
        res += " "
        value = res
    return value


def aspect_function(internal_loop: int, tainted: str) -> str:
    value = ''
    res = value
    for j in range(internal_loop):
        # res = add_aspect(res, join_aspect("_", (tainted, "_", tainted)))
        # value = res
        # res = add_aspect(res, tainted)
        # value = res
        # res = add_aspect(res, " ")
        # value = res

        # res = join_aspect("_", ("foobar", "_", tainted))
        res = add_aspect(tainted, tainted)
        # res = add_aspect(res, join_aspect("_", (tainted, "_", tainted)))
        # value = res
        # res = add_aspect(res, tainted)
        # value = res
        # res = add_aspect(res, " ")
        value = res

    return value


def launch_function(func: Callable, internal_loop: float, caller_loop: int) -> float:
    start = time.time()
    print("func %s internal_loop %s caller_loop %s" % (func, internal_loop, caller_loop))
    for i in range(caller_loop):
        tainted_value = new_request(ENABLE_PROPAGATION)
        func(internal_loop, tainted_value)
    end = time.time()
    print("Number of tainted objects %s.\n"
          "Number of contexts: %s\n"
          "Initializer size: %s\n"
          "Initializer active_map_addreses size: %s\n"
          "Memory usage MB after contexts_reset: %s\n"
          "------------------------------------------------" %
          (num_objects_tainted(), num_contexts(), initializer_size(), active_map_addreses_size(),
           psutil.Process(os.getpid()).memory_info().rss / 1024 ** 2))
    print_created()
    return (end - start) * 1000


def main() -> None:
    setup(bytes.join, bytearray.join)
    # internal_loop = 1000
    # caller_loop = 100
    # repetitions = 0
    # repetitions_end = 5

    internal_loop = 100
    caller_loop = 100
    repetitions = 0
    repetitions_end = 10

    total_time_normal = 0.0
    total_time_aspect = 0.0
    process_times_normal = []
    process_times_aspect = []
    relative_overheads = []
    absolute_overheads = []

    print('Starting...')
    while repetitions < repetitions_end:
        print("-" * 50)
        repetitions += 1
        print('Iteration %d' % repetitions)
        process_time_aspect = launch_function(aspect_function, internal_loop, caller_loop)


    contexts_reset()
    print("FINAL REPORT" + "-"*50)
    print("Number of tainted objects %s.\n"
          "Number of contexts: %s\n"
          "Initializer size: %s\n"
          "Initializer active_map_addreses size: %s\n"
          "Memory usage MB after contexts_reset: %s\n"
          "------------------------------------------------" %
          (num_objects_tainted(), num_contexts(), initializer_size(), active_map_addreses_size(),
           psutil.Process(os.getpid()).memory_info().rss / 1024 ** 2))
    print_created()


if __name__ == '__main__':
    main()
