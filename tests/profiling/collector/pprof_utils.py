import ctypes
from enum import Enum
import glob
import os
import re
from typing import TYPE_CHECKING
from typing import Optional
from typing import Sequence
from typing import Union
from typing import cast

import zstandard as zstd

from tests.profiling.collector.lock_utils import LineNo


UINT64_MAX = (1 << 64) - 1
DEBUG_TEST = False


def _protobuf_version() -> tuple[int, int, int]:
    """Check if protobuf version is post 3.12"""
    import google.protobuf

    from ddtrace.internal.utils.version import parse_version

    return parse_version(google.protobuf.__version__)


if TYPE_CHECKING:
    from tests.profiling.collector import pprof_pb2  # pyright: ignore[reportMissingModuleSource]
else:
    # Load the appropriate pprof_pb2 module
    _pb_version = _protobuf_version()
    for v in [(4, 21), (3, 19), (3, 12)]:
        if _pb_version >= v:
            import sys

            pprof_module = "tests.profiling.collector.pprof_%s%s_pb2" % v
            __import__(pprof_module)
            pprof_pb2 = sys.modules[pprof_module]
            break
    else:
        from tests.profiling.collector import pprof_3_pb2 as pprof_pb2  # type: ignore[no-redef]


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
    def __init__(self, function_name: str, filename: str, line_no: int) -> None:
        self.function_name = function_name
        self.filename = filename
        self.line_no = line_no

    def __repr__(self) -> str:
        filename = self.filename or "<any file>"
        line_no = str(self.line_no) if self.line_no != -1 else "<any line>"
        return f"{filename}:{self.function_name}:{line_no}"


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
        thread_id: Optional[int] = None,
        thread_name: Optional[str] = None,
        class_name: Optional[str] = None,
        task_id: Optional[int] = None,
        task_name: Optional[str] = None,
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
    def __init__(
        self, locations: Optional[Sequence[StackLocation]] = None, exception_type: Optional[str] = None, *args, **kwargs
    ) -> None:
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


def merge_profiles(profiles: list[pprof_pb2.Profile]) -> pprof_pb2.Profile:
    """Merge multiple pprof profiles into one.

    This combines string tables, locations, functions, and samples from all profiles.
    Samples with identical stacks are NOT aggregated - they're kept separate.
    """
    if not profiles:
        raise ValueError("No profiles to merge")
    if len(profiles) == 1:
        return profiles[0]

    merged = pprof_pb2.Profile()

    # Maps: (old_profile_idx, old_id) -> new_id
    string_map: dict[tuple[int, int], int] = {}
    function_map: dict[tuple[int, int], int] = {}
    location_map: dict[tuple[int, int], int] = {}

    # String table: index 0 must be empty string
    merged.string_table.append("")
    string_map_global: dict[str, int] = {"": 0}

    def get_or_add_string(profile_idx: int, old_idx: int, old_string_table: Sequence[str]) -> int:
        key = (profile_idx, old_idx)
        if key in string_map:
            return string_map[key]
        s = old_string_table[old_idx]
        if s in string_map_global:
            new_idx = string_map_global[s]
        else:
            new_idx = len(merged.string_table)
            merged.string_table.append(s)
            string_map_global[s] = new_idx
        string_map[key] = new_idx
        return new_idx

    # First pass: merge sample_types from first profile (assume all have same types)
    first = profiles[0]
    for st in first.sample_type:
        new_st = merged.sample_type.add()
        new_st.type = get_or_add_string(0, st.type, first.string_table)
        new_st.unit = get_or_add_string(0, st.unit, first.string_table)

    # Merge each profile
    for pidx, profile in enumerate(profiles):
        # Merge functions
        for func in profile.function:
            new_func = merged.function.add()
            new_func.id = len(merged.function)
            new_func.name = get_or_add_string(pidx, func.name, profile.string_table)
            new_func.system_name = get_or_add_string(pidx, func.system_name, profile.string_table)
            new_func.filename = get_or_add_string(pidx, func.filename, profile.string_table)
            new_func.start_line = func.start_line
            function_map[(pidx, func.id)] = new_func.id

        # Merge locations
        for loc in profile.location:
            new_loc = merged.location.add()
            new_loc.id = len(merged.location)
            new_loc.address = loc.address
            for line in loc.line:
                new_line = new_loc.line.add()
                new_line.function_id = function_map[(pidx, line.function_id)]
                new_line.line = line.line
            location_map[(pidx, loc.id)] = new_loc.id

        # Merge samples
        for sample in profile.sample:
            new_sample = merged.sample.add()
            for loc_id in sample.location_id:
                new_sample.location_id.append(location_map[(pidx, loc_id)])
            for val in sample.value:
                new_sample.value.append(val)
            for label in sample.label:
                new_label = new_sample.label.add()
                new_label.key = get_or_add_string(pidx, label.key, profile.string_table)
                if label.str:
                    new_label.str = get_or_add_string(pidx, label.str, profile.string_table)
                new_label.num = label.num
                if label.num_unit:
                    new_label.num_unit = get_or_add_string(pidx, label.num_unit, profile.string_table)

    return merged


def parse_profile(filename: str) -> pprof_pb2.Profile:
    with open(filename, "rb") as fp:
        dctx = zstd.ZstdDecompressor()
        serialized_data = dctx.stream_reader(fp).read()
    profile = pprof_pb2.Profile()
    profile.ParseFromString(serialized_data)
    return profile


def parse_newest_profile(
    filename_prefix: str, assert_samples: bool = True, allow_penultimate: bool = False
) -> pprof_pb2.Profile:
    """Parse the newest profile that has given filename prefix. The profiler
    outputs profile file with following naming convention:
    <filename_prefix>.<pid>.<counter>.pprof, and in tests, we'd want to parse
    the newest profile that has given filename prefix.
    """
    files = glob.glob(filename_prefix + ".*.pprof")

    if not files:
        raise FileNotFoundError(
            f"No profile files found for {filename_prefix}, all pprof files: {glob.glob('*.pprof') or '(none)'}"
        )

    # Sort files by logical timestamp (i.e. the sequence number, which is monotonically increasing);
    # this approach is more reliable than filesystem timestamps, especially when files are created rapidly.
    files.sort(key=lambda f: int(f.rsplit(".", 2)[-2]))
    filename = files[-1]
    with open(filename, "rb") as fp:
        dctx = zstd.ZstdDecompressor()
        serialized_data = dctx.stream_reader(fp).read()

    profile = pprof_pb2.Profile()
    profile.ParseFromString(serialized_data)

    if allow_penultimate and len(profile.sample) == 0 and len(files) > 1:
        # The newest profile file may be empty if it was just created and has not yet accumulated samples.
        # In this case, we try to parse the (second-to-last) file instead, which is more likely
        # to contain complete data. We temporarily rename the newest file so parse_newest_profile picks
        # the previous one.
        temp_filename = filename + ".temp"
        os.rename(filename, temp_filename)
        try:
            return parse_newest_profile(filename_prefix, assert_samples, allow_penultimate=False)
        finally:
            os.rename(temp_filename, filename)

    if assert_samples:
        assert len(profile.sample) > 0, "No samples found in profile"

    return profile


def get_sample_type_index(profile: pprof_pb2.Profile, value_type: str) -> int:
    return next(
        i for i, sample_type in enumerate(profile.sample_type) if profile.string_table[sample_type.type] == value_type
    )


def get_samples_with_value_type(profile: pprof_pb2.Profile, value_type: str) -> list[pprof_pb2.Sample]:
    value_type_idx = get_sample_type_index(profile, value_type)
    return [sample for sample in profile.sample if sample.value[value_type_idx] > 0]


def get_samples_with_label_key(profile: pprof_pb2.Profile, key: str) -> list[pprof_pb2.Sample]:
    return [sample for sample in profile.sample if get_label_with_key(profile.string_table, sample, key)]


def get_label_with_key(string_table: Sequence[str], sample: pprof_pb2.Sample, key: str) -> Optional[pprof_pb2.Label]:
    return next((label for label in sample.label if string_table[label.key] == key), None)


def get_location_with_id(profile: pprof_pb2.Profile, location_id: int) -> pprof_pb2.Location:
    return next(location for location in profile.location if location.id == location_id)


def get_function_with_id(profile: pprof_pb2.Profile, function_id: int) -> pprof_pb2.Function:
    return next(function for function in profile.function if function.id == function_id)


def get_location_from_id(profile: pprof_pb2.Profile, location_id: int) -> StackLocation:
    """Get a StackLocation tuple from a location ID.

    Args:
        profile: The pprof profile containing location and function data
        location_id: The location ID to look up

    Returns:
        A StackLocation with function_name, filename, and line_no
    """
    location = get_location_with_id(profile, location_id)
    line = location.line[0]
    function = get_function_with_id(profile, line.function_id)
    function_name = profile.string_table[function.name]
    filename = profile.string_table[function.filename]
    line_no = line.line
    return StackLocation(function_name=function_name, filename=filename, line_no=line_no)


def assert_lock_events_of_type(
    profile: pprof_pb2.Profile,
    expected_events: Sequence[LockEvent],
    event_type: LockEventType,
):
    samples = get_samples_with_value_type(
        profile, "lock-acquire" if event_type == LockEventType.ACQUIRE else "lock-release"
    )
    assert len(samples) >= len(expected_events), (
        f"Expected at least {len(expected_events)} samples, found only {len(samples)}"
    )

    # sort the samples and expected events by lock name, which is <filename>:<line>:<lock_name>
    # when the lock_name exists, otherwise <filename>:<line>
    assert all(get_label_with_key(profile.string_table, sample, "lock name") for sample in samples), (
        "All samples should have the label 'lock name'"
    )
    samples_dict = {
        profile.string_table[
            cast(pprof_pb2.Label, get_label_with_key(profile.string_table, sample, "lock name")).str
        ]: sample
        for sample in samples
    }
    for expected_event in expected_events:
        if expected_event.lock_name is None:
            key = f"{expected_event.filename}:{expected_event.linenos.create}"
        else:
            key = f"{expected_event.filename}:{expected_event.linenos.create}:{expected_event.lock_name}"
        assert key in samples_dict, f"Expected lock event {key} not found"
        assert_lock_event(profile, samples_dict[key], expected_event)


def assert_lock_events(
    profile: pprof_pb2.Profile,
    expected_acquire_events: Union[Sequence[LockEvent], None] = None,
    expected_release_events: Union[Sequence[LockEvent], None] = None,
    print_samples_on_failure: bool = False,
) -> None:
    try:
        if expected_acquire_events:
            assert_lock_events_of_type(profile, expected_acquire_events, LockEventType.ACQUIRE)
        if expected_release_events:
            assert_lock_events_of_type(profile, expected_release_events, LockEventType.RELEASE)
    except AssertionError as e:
        if print_samples_on_failure:
            print_all_samples(profile)

        raise e


def assert_str_label(string_table: Sequence[str], sample, key: str, expected_value: Optional[str]):
    if not expected_value:
        return

    label = get_label_with_key(string_table, sample, key)
    # We use fullmatch to ensure that the whole string matches the expected value
    # and not just a substring
    assert label is not None, f"Label {key} not found in sample"
    assert re.fullmatch(expected_value, string_table[label.str]), (
        f"Expected {expected_value} got {string_table[label.str]} for label {key}"
    )


def assert_num_label(string_table: Sequence[str], sample, key: str, expected_value: Optional[int]):
    if not expected_value:
        return

    label = get_label_with_key(string_table, sample, key)
    assert label is not None, f"Label {key} not found in sample"
    assert label.num == expected_value, f"Expected {expected_value} got {label.num} for label {key}"


def assert_base_event(string_table: Sequence[str], sample: pprof_pb2.Sample, expected_event: EventBaseClass):
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
    assert lock_name_label is not None, "Lock name label not found in sample"
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


def assert_sample_has_locations(
    profile: pprof_pb2.Profile, sample: pprof_pb2.Sample, expected_locations: Optional[Sequence[StackLocation]]
) -> None:
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
            function_name_matches = function_name.endswith(expected_locations[expected_locations_idx].function_name)
            filename_matches = expected_locations[expected_locations_idx].filename == "" or re.fullmatch(
                expected_locations[expected_locations_idx].filename, filename
            )
            line_no_matches = (
                expected_locations[expected_locations_idx].line_no == -1
                or line_no == expected_locations[expected_locations_idx].line_no
            )

            if function_name_matches and filename_matches and line_no_matches:
                expected_locations_idx += 1
                if expected_locations_idx == len(expected_locations):
                    checked = True

    for loc in sample_loc_strs:
        if DEBUG_TEST:
            print(loc)

    assert checked, f"Expected locations {expected_locations} not found in sample locations: {sample_loc_strs}"


def assert_stack_event(
    profile: pprof_pb2.Profile,
    sample: pprof_pb2.Sample,
    expected_event: StackEvent,
    print_samples_on_failure: bool = False,
) -> None:
    try:
        # Check that the sample has label "exception type" with value (no-op if expected_event.exception_type is None)
        assert_str_label(profile.string_table, sample, "exception type", expected_event.exception_type)
        assert_sample_has_locations(profile, sample, expected_event.locations)
        assert_base_event(profile.string_table, sample, expected_event)
    except AssertionError as e:
        if print_samples_on_failure:
            print_all_samples(profile)

        raise e


def assert_profile_has_sample(
    profile: pprof_pb2.Profile,
    samples: Sequence[pprof_pb2.Sample],
    expected_sample: StackEvent,
    print_samples_on_failure: bool = False,
) -> None:
    """
    Assert that at least one of the Samples passed matches the expected Sample.

    - The expected Sample matches if its locations are a (not necessarily contiguous) subsequence of the locations in
      any of the Samples passed.
    - The innermost/deepest function in the stack should be the first location
    """

    found = False
    for sample in samples:
        try:
            # Pass print_samples_on_failure=False since we will print them later if needed
            assert_stack_event(profile, sample, expected_sample, print_samples_on_failure=False)
            found = True
            break
        except AssertionError as e:
            # flip the flag to print the error message
            if DEBUG_TEST:
                print(e)

    error_description = "Expected samples not found in profile "
    if not found:
        error_description += str(expected_sample.locations)
        if expected_sample.task_name:
            error_description += ", task name " + expected_sample.task_name

        if expected_sample.thread_name:
            error_description += ", thread name " + expected_sample.thread_name

        if print_samples_on_failure:
            print_all_samples(profile)

    assert found, error_description


def assert_profile_does_not_have_sample(
    profile: pprof_pb2.Profile,
    samples: Sequence[pprof_pb2.Sample],
    expected_sample: StackEvent,
    print_samples_on_failure: bool = False,
) -> None:
    """
    Assert that no Sample passed matches the expected Sample.

    - The expected Sample matches if its locations are a (not necessarily contiguous) subsequence of the locations in
      any of the Samples passed.
    - The innermost/deepest function in the stack should be the first location
    """
    try:
        assert_profile_has_sample(profile, samples, expected_sample, print_samples_on_failure=False)
    except AssertionError:
        return

    if print_samples_on_failure:
        print_all_samples(profile)

    error_description = "Expected sample found in profile: "
    error_description += str(expected_sample.locations)
    if expected_sample.task_name:
        error_description += ", task name " + expected_sample.task_name

    if expected_sample.thread_name:
        error_description += ", thread name " + expected_sample.thread_name

    if print_samples_on_failure:
        print_all_samples(profile)

    raise AssertionError(error_description)


def print_all_samples(profile: pprof_pb2.Profile) -> None:
    """Print all samples in the profile with function name, filename, and line number."""
    for sample_idx, sample in enumerate(profile.sample):
        print(f"Sample {sample_idx}:")
        print("Non-zero values:")
        for i, val in enumerate(sample.value):
            if val != 0:
                st = profile.sample_type[i]
                type_name = profile.string_table[st.type]
                unit = profile.string_table[st.unit]
                print(f"  {type_name} ({unit}): {val}")
        print("Labels:")
        for label in sample.label:
            print(f"  {profile.string_table[label.key]}: {profile.string_table[label.str]}")
        print("Locations:")
        for location_id in sample.location_id:
            location = get_location_with_id(profile, location_id)
            for line in location.line:
                function = get_function_with_id(profile, line.function_id)
                function_name = profile.string_table[function.name]
                filename = profile.string_table[function.filename]
                line_no = line.line
                print(f"  {filename}:{function_name}:{line_no}")
        print()
