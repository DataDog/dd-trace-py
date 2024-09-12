import ctypes
from enum import Enum
import glob
import re
import typing

import lz4.frame

from ddtrace.profiling.exporter.pprof import pprof_pb2
from tests.profiling.collector.lock_utils import LineNo


UINT64_MAX = (1 << 64) - 1


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


class LockEventType(Enum):
    ACQUIRE = 1
    RELEASE = 2


# A simple class to hold the expected attributes of a lock sample.
class LockEvent:
    def __init__(
        self,
        event_type: LockEventType,
        caller_name: str,
        filename: str,
        linenos: LineNo,
        lock_name: typing.Union[str, None] = None,
        span_id=None,
        trace_resource=None,
        trace_type=None,
        task_id: typing.Union[int, None] = None,
        task_name: typing.Union[str, None] = None,
        thread_id: typing.Union[int, None] = None,
        thread_name: typing.Union[str, None] = None,
    ):
        self.event_type = event_type
        self.caller_name = caller_name
        self.filename = filename
        self.linenos = linenos
        self.lock_name = lock_name
        self.span_id = reinterpret_int_as_int64(clamp_to_uint64(span_id)) if span_id else None
        self.trace_endpoint = trace_resource
        self.trace_type = trace_type
        self.task_id = task_id
        self.task_name = task_name
        self.thread_id = thread_id
        self.thread_name = thread_name


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


def get_samples_with_value_type(profile: pprof_pb2.Profile, value_type: str) -> typing.List[pprof_pb2.Sample]:
    value_type_idx = get_sample_type_index(profile, value_type)
    return [sample for sample in profile.sample if sample.value[value_type_idx] > 0]


def get_label_with_key(string_table: typing.Dict[int, str], sample: pprof_pb2.Sample, key: str) -> pprof_pb2.Label:
    return next(label for label in sample.label if string_table[label.key] == key)


def get_location_with_id(profile: pprof_pb2.Profile, location_id: int) -> pprof_pb2.Location:
    return next(location for location in profile.location if location.id == location_id)


def get_function_with_id(profile: pprof_pb2.Profile, function_id: int) -> pprof_pb2.Function:
    return next(function for function in profile.function if function.id == function_id)


def assert_lock_events_of_type(
    profile: pprof_pb2.Profile,
    expected_events: typing.List[LockEvent],
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
    expected_acquire_events: typing.Union[typing.List[LockEvent], None] = None,
    expected_release_events: typing.Union[typing.List[LockEvent], None] = None,
):
    if expected_acquire_events:
        assert_lock_events_of_type(profile, expected_acquire_events, LockEventType.ACQUIRE)
    if expected_release_events:
        assert_lock_events_of_type(profile, expected_release_events, LockEventType.RELEASE)


def assert_lock_event(profile, sample: pprof_pb2.Sample, expected_event: LockEvent):
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

    if expected_event.span_id is not None:
        span_id_label = get_label_with_key(profile.string_table, sample, "span id")
        assert span_id_label.num == expected_event.span_id, "Expected span_id {} got {}".format(
            expected_event.span_id, span_id_label.num
        )

    if expected_event.trace_type is not None:
        trace_type_label = get_label_with_key(profile.string_table, sample, "trace type")
        assert (
            profile.string_table[trace_type_label.str] == expected_event.trace_type
        ), "Expected trace_type {} got {}".format(expected_event.trace_type, profile.string_table[trace_type_label.str])

    if expected_event.trace_endpoint is not None:
        trace_endpoint_label = get_label_with_key(profile.string_table, sample, "trace endpoint")
        assert (
            profile.string_table[trace_endpoint_label.str] == expected_event.trace_endpoint
        ), "Expected trace endpoint {} got {}".format(
            expected_event.trace_endpoint, profile.string_table[trace_endpoint_label.str]
        )

    if expected_event.task_id is not None:
        task_id_label = get_label_with_key(profile.string_table, sample, "task id")
        assert task_id_label.num == expected_event.task_id, "Expected task_id {} got {}".format(
            expected_event.task_id, task_id_label.num
        )

    if expected_event.task_name is not None:
        task_name_label = get_label_with_key(profile.string_table, sample, "task name")
        assert re.fullmatch(
            expected_event.task_name,
            profile.string_table[task_name_label.str],
        ), "Expected task_name {} got {}".format(expected_event.task_name, profile.string_table[task_name_label.str])

    if expected_event.thread_id:
        thread_id_label = get_label_with_key(profile.string_table, sample, "thread id")
        assert thread_id_label.num == expected_event.thread_id, "Expected thread_id {} got {}".format(
            expected_event.thread_id, thread_id_label.num
        )

    if expected_event.thread_name:
        thread_name_label = get_label_with_key(profile.string_table, sample, "thread name")
        assert (
            profile.string_table[thread_name_label.str] == expected_event.thread_name
        ), "Expected thread_name {} got {}".format(
            expected_event.thread_name, profile.string_table[thread_name_label.str]
        )
