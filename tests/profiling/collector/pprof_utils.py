import ctypes
from enum import Enum
import glob
import os
import re
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

import lz4.frame

from ddtrace.profiling.exporter.pprof import pprof_pb2
from tests.profiling.collector.lock_utils import LineNo


UINT64_MAX = (1 << 64) - 1
DEBUG_TEST = True


# Clamp the value to the range [0, UINT64_MAX] as done in clamp_to_uint64_unsigned
def clamp_to_uint64(value: int) -> int:
    if value > UINT64_MAX:
        return UINT64_MAX
    if value < 0:
        return 0
    return value


# pprof uses int64 so we need to reinterpret what's returned from clamp_to_uint64
# as an int64 as done in C++'s reinterpret_cast<int64_t>(value)
def reinterpret_int_as_int64(value: int) -> int:
    return ctypes.c_int64(value).value


class StackLocation:
    def __init__(self, function_name: str, filename: str, line_no: int):
        self.function_name = function_name
        self.filename = filename
        self.line_no = line_no

    def __repr__(self):
        return f"{self.filename}:{self.function_name}:{self.line_no}"


class LockEventType(Enum):
    ACQUIRE = 1
    RELEASE = 2


class EventBaseClass:
    def __init__(
        self,
        span_id: Optional[int] = None,
        local_root_span_id: Optional[int] = None,
        trace_type: Optional[str] = None,
        trace_endpoint: Optional[str] = None,
        thread_id: Union[int, None] = None,
        thread_name: Union[str, None] = None,
        class_name: Union[str, None] = None,
        task_id: Union[int, None] = None,
        task_name: Union[str, None] = None,
    ):
        self.span_id = reinterpret_int_as_int64(clamp_to_uint64(span_id)) if span_id else None
        self.local_root_span_id = (
            reinterpret_int_as_int64(clamp_to_uint64(local_root_span_id)) if local_root_span_id else None
        )
        self.trace_type = trace_type
        self.trace_endpoint = trace_endpoint
        self.thread_id = thread_id
        self.thread_name = thread_name
        self.class_name = class_name
        self.task_id = task_id
        self.task_name = task_name


class StackEvent(EventBaseClass):
    def __init__(self, locations: Optional[Any] = None, exception_type=None, *args, **kwargs):
        self.locations = locations
        self.exception_type = exception_type
        super().__init__(*args, **kwargs)


# A simple class to hold the expected attributes of a lock sample.
class LockEvent(EventBaseClass):
    def __init__(
        self,
        event_type: LockEventType,
        caller_name: str,
        filename: str,
        linenos: LineNo,
        lock_name: Union[str, None] = None,
        *args,
        **kwargs,
    ):
        self.event_type = event_type
        self.caller_name = caller_name
        self.filename = filename
        self.linenos = linenos
        self.lock_name = lock_name
        super().__init__(*args, **kwargs)


class LockAcquireEvent(LockEvent):
    def __init__(self, *args, **kwargs):
        super().__init__(event_type=LockEventType.ACQUIRE, *args, **kwargs)


class LockReleaseEvent(LockEvent):
    def __init__(self, *args, **kwargs):
        super().__init__(event_type=LockEventType.RELEASE, *args, **kwargs)


def parse_profile(filename_prefix: str):
    files = glob.glob(filename_prefix + ".*")
    files.sort()
    filename = files[-1]
    with lz4.frame.open(filename) as fp:
        serialized_data = fp.read()
    profile = pprof_pb2.Profile()
    profile.ParseFromString(serialized_data)
    assert len(profile.sample) > 0, "No samples found in profile"
    return profile


def get_sample_type_index(profile: pprof_pb2.Profile, value_type: str) -> int:
    return next(
        i for i, sample_type in enumerate(profile.sample_type) if profile.string_table[sample_type.type] == value_type
    )


def get_samples_with_value_type(profile: pprof_pb2.Profile, value_type: str) -> List[pprof_pb2.Sample]:
    value_type_idx = get_sample_type_index(profile, value_type)
    return [sample for sample in profile.sample if sample.value[value_type_idx] > 0]


def get_samples_with_label_key(profile: pprof_pb2.Profile, key: str) -> List[pprof_pb2.Sample]:
    return [sample for sample in profile.sample if get_label_with_key(profile.string_table, sample, key)]


def get_label_with_key(string_table: Dict[int, str], sample: pprof_pb2.Sample, key: str) -> pprof_pb2.Label:
    return next((label for label in sample.label if string_table[label.key] == key), None)


def get_location_with_id(profile: pprof_pb2.Profile, location_id: int) -> pprof_pb2.Location:
    return next(location for location in profile.location if location.id == location_id)


def get_function_with_id(profile: pprof_pb2.Profile, function_id: int) -> pprof_pb2.Function:
    return next(function for function in profile.function if function.id == function_id)


def assert_lock_events_of_type(
    profile: pprof_pb2.Profile,
    expected_events: List[LockEvent],
    event_type: LockEventType,
):
    samples = get_samples_with_value_type(
        profile, "lock-acquire" if event_type == LockEventType.ACQUIRE else "lock-release"
    )
    assert len(samples) >= len(expected_events), "Expected at least {} samples, found only {}".format(
        len(expected_events), len(samples)
    )

    # sort the samples and expected events by lock name, which is <filename>:<line>:<lock_name>
    # when the lock_name exists, otherwise <filename>:<line>
    assert all(
        get_label_with_key(profile.string_table, sample, "lock name") for sample in samples
    ), "All samples should have the label 'lock name'"
    samples = {
        profile.string_table[get_label_with_key(profile.string_table, sample, "lock name").str]: sample
        for sample in samples
    }
    for expected_event in expected_events:
        if expected_event.lock_name is None:
            key = "{}:{}".format(expected_event.filename, expected_event.linenos.create)
        else:
            key = "{}:{}:{}".format(expected_event.filename, expected_event.linenos.create, expected_event.lock_name)
        assert key in samples, "Expected lock event {} not found".format(key)
        assert_lock_event(profile, samples[key], expected_event)


def assert_lock_events(
    profile: pprof_pb2.Profile,
    expected_acquire_events: Union[List[LockEvent], None] = None,
    expected_release_events: Union[List[LockEvent], None] = None,
):
    if expected_acquire_events:
        assert_lock_events_of_type(profile, expected_acquire_events, LockEventType.ACQUIRE)
    if expected_release_events:
        assert_lock_events_of_type(profile, expected_release_events, LockEventType.RELEASE)


def assert_str_label(string_table: Dict[int, str], sample, key: str, expected_value: Optional[str]):
    if expected_value:
        label = get_label_with_key(string_table, sample, key)
        # We use fullmatch to ensure that the whole string matches the expected value
        # and not just a substring
        assert label is not None, "Label {} not found in sample".format(key)
        assert re.fullmatch(expected_value, string_table[label.str]), "Expected {} got {} for label {}".format(
            expected_value, string_table[label.str], key
        )


def assert_num_label(string_table: Dict[int, str], sample, key: str, expected_value: Optional[int]):
    if expected_value:
        label = get_label_with_key(string_table, sample, key)
        assert label.num == expected_value, "Expected {} got {} for label {}".format(expected_value, label.num, key)


def assert_base_event(string_table: Dict[int, str], sample: pprof_pb2.Sample, expected_event: EventBaseClass):
    assert_num_label(string_table, sample, "span id", expected_event.span_id)
    assert_num_label(string_table, sample, "local root span id", expected_event.local_root_span_id)
    assert_str_label(string_table, sample, "trace type", expected_event.trace_type)
    assert_str_label(string_table, sample, "trace endpoint", expected_event.trace_endpoint)
    assert_num_label(string_table, sample, "thread id", expected_event.thread_id)
    assert_str_label(string_table, sample, "thread name", expected_event.thread_name)
    assert_str_label(string_table, sample, "class name", expected_event.class_name)
    assert_num_label(string_table, sample, "task id", expected_event.task_id)
    assert_str_label(string_table, sample, "task name", expected_event.task_name)


def assert_lock_event(profile: pprof_pb2.Profile, sample: pprof_pb2.Sample, expected_event: LockEvent):
    # Check that the sample has label "lock name" with value
    # filename:self.lock_linenos.create:lock_name
    lock_name_label = get_label_with_key(profile.string_table, sample, "lock name")
    if expected_event.lock_name is None:
        expected_lock_name = "{}:{}".format(expected_event.filename, expected_event.linenos.create)
    else:
        expected_lock_name = "{}:{}:{}".format(
            expected_event.filename, expected_event.linenos.create, expected_event.lock_name
        )
    actual_lock_name = profile.string_table[lock_name_label.str]
    assert actual_lock_name == expected_lock_name, "Expected lock name {} got {}".format(
        expected_lock_name, actual_lock_name
    )
    # location_id[0] is the 'leaf' location
    location_id = sample.location_id[0]
    location = get_location_with_id(profile, location_id)
    # We expect only one line element as we don't have inlined functions
    line = location.line[0]
    # We expect the function name to be the caller's name
    function = get_function_with_id(profile, line.function_id)
    assert profile.string_table[function.name] == expected_event.caller_name, "Expected caller {} got {}".format(
        expected_event.caller_name, profile.string_table[function.name]
    )
    if expected_event.event_type == LockEventType.ACQUIRE:
        assert line.line == expected_event.linenos.acquire, "Expected line {} got {}".format(
            expected_event.linenos.acquire, line.line
        )
    elif expected_event.event_type == LockEventType.RELEASE:
        assert line.line == expected_event.linenos.release, "Expected line {} got {}".format(
            expected_event.linenos.release, line.line
        )

    assert_base_event(profile.string_table, sample, expected_event)


def assert_sample_has_locations(profile, sample, expected_locations: Optional[List[StackLocation]]):
    if not expected_locations:
        return

    expected_locations_idx = 0
    checked = False

    # For debug printing
    sample_loc_strs = []
    # in a sample there can be multiple locations, we need to check
    # whether there's a consecutive subsequence of locations that match
    # the expected locations
    for location_id in sample.location_id:
        location = get_location_with_id(profile, location_id)
        line = location.line[0]
        function = get_function_with_id(profile, line.function_id)
        function_name = profile.string_table[function.name]
        filename = os.path.basename(profile.string_table[function.filename])
        line_no = line.line
        sample_loc_strs.append(f"{filename}:{function_name}:{line_no}")

        if expected_locations_idx < len(expected_locations):
            if (
                function_name.endswith(expected_locations[expected_locations_idx].function_name)
                and re.fullmatch(expected_locations[expected_locations_idx].filename, filename)
                and line_no == expected_locations[expected_locations_idx].line_no
            ):
                expected_locations_idx += 1
                if expected_locations_idx == len(expected_locations):
                    checked = True

    for loc in sample_loc_strs:
        if DEBUG_TEST:
            print(loc)

    assert checked, "Expected locations {} not found in sample locations: {}".format(
        expected_locations, sample_loc_strs
    )


def assert_stack_event(profile: pprof_pb2.Profile, sample: pprof_pb2.Sample, expected_event: StackEvent):
    # Check that the sample has label "exception type" with value
    assert_str_label(profile.string_table, sample, "exception type", expected_event.exception_type)
    assert_sample_has_locations(profile, sample, expected_event.locations)
    assert_base_event(profile.string_table, sample, expected_event)


def assert_profile_has_sample(
    profile: pprof_pb2.Profile,
    samples: List[pprof_pb2.Sample],
    expected_sample: StackEvent,
):
    found = False
    for sample in samples:
        try:
            assert_stack_event(profile, sample, expected_sample)
            found = True
            break
        except AssertionError as e:
            # flip the flag to print the error message
            if DEBUG_TEST:
                print(e)

    assert found, "Expected samples not found in profile"
