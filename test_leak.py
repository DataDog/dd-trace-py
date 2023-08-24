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
from ddtrace.appsec.iast._taint_tracking import destroy_context
from ddtrace.appsec.iast._taint_tracking import num_contexts
from ddtrace.appsec.iast._taint_tracking import initializer_size
from ddtrace.appsec.iast._taint_tracking import active_map_addreses_size
from ddtrace.appsec.iast._taint_tracking.aspects import join_aspect, add_aspect

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


def new_request(enable_propagation: bool) -> str:
    tainted = b"my_string".decode("ascii")
    # destroy_context()
    contexts_reset()
    create_context()

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
    if ENABLE_CHECKING:
        ranges = get_ranges(res)
    if DEBUG_MODE:
        pass
    return value


def aspect_function(internal_loop: int, tainted: str) -> str:
    value = ''
    res = value
    for j in range(internal_loop):
        res = add_aspect(res, join_aspect("_", (tainted, "_", tainted)))
        value = res
        res = add_aspect(res, tainted)
        value = res
        res = add_aspect(res, " ")
        value = res

        ranges = get_ranges(res)
        print(ranges)
    if ENABLE_CHECKING:
        ranges = get_ranges(res)
        print(ranges)
        # print("RANGES: {}".format(len(ranges)))
        # assert len(ranges) == 100, "Fail. ranges are %s" % len(ranges)
    if DEBUG_MODE:
        pass  # print(get_ranges(res))
    return value


def launch_function(func: Callable, internal_loop: float, caller_loop: int) -> float:
    from ddtrace.appsec.iast._taint_tracking import num_objects_tainted
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
          "Num created tobjects: %s\n"
          "Num destroyed tobjects: %s\n"
          "------------------------------------------------" %
          (num_objects_tainted(), num_contexts(), initializer_size(), active_map_addreses_size(),
           psutil.Process(os.getpid()).memory_info().rss / 1024 ** 2,
          num_created_tobjects(), num_destroyed_tobjects()))
    return (end - start) * 1000


def main() -> None:
    internal_loop = 1000
    caller_loop = 100
    repetitions = 0
    repetitions_end = 20

    if not DEBUG_MODE:
        print('Warming up...')
        for i in range(10):
            launch_function(normal_function, internal_loop, caller_loop)
            launch_function(aspect_function, internal_loop, caller_loop)

    total_time_normal = 0.0
    total_time_aspect = 0.0
    process_times_normal = []
    process_times_aspect = []
    relative_overheads = []
    absolute_overheads = []

    print('Starting...')
    while repetitions < repetitions_end:
        repetitions += 1
        if not DEBUG_MODE:
            print('Iteration %d' % repetitions)

            process_time_normal = launch_function(normal_function, internal_loop, caller_loop)
            print('NORMAL:')
            print('\tThis iteration: %s' % process_time_normal)
            print('\tThis iteration per request: %s' % (process_time_normal / caller_loop))
            process_times_normal.append(process_time_normal)

        process_time_aspect = launch_function(aspect_function, internal_loop, caller_loop)

        if not DEBUG_MODE:
            print('ASPECT:')
            print('\tThis iteration: %s' % process_time_aspect)
            print('\tThis iteration per request: %s' % (process_time_aspect / caller_loop))
            process_times_aspect.append(process_time_aspect)
            absolute_overhead = process_time_aspect - process_time_normal
            absolute_overheads.append(absolute_overhead)
            relative_overhead = absolute_overhead / process_time_normal
            relative_overheads.append(relative_overhead)
            print('\tAspect relative overhead: %s%%' % (relative_overhead * 100))
            print('\tAspect absolute overhead per request: {}'.format(absolute_overhead / caller_loop))
            print()

    if not DEBUG_MODE:
        print('Median normal time: {}'.format(median(process_times_normal)))
        print('Median normal time per request: {}'.format(median(process_times_normal) / caller_loop))
        print('Median aspect time: {}'.format(median(process_times_aspect)))
        print('Median aspect time per request: {}'.format(median(process_times_aspect) / caller_loop))
        print('Median aspect relative overhead: %s%%' % (median(relative_overheads) * 100))
        print('Median aspect absolute overhead: {}'.format(median(absolute_overheads)))
        print('Median aspect absolute overhead per request: {}'.format(median(absolute_overheads) / caller_loop))

if __name__ == '__main__':
    main()
