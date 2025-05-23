# -*- coding: utf-8 -*-
from ast import literal_eval
import asyncio
import logging
from multiprocessing.pool import ThreadPool
import random
import re
import sys
from time import sleep

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import are_all_text_all_ranges
from ddtrace.appsec._iast._taint_tracking import debug_taint_map
from ddtrace.appsec._iast._taint_tracking import get_range_by_hash
from ddtrace.appsec._iast._taint_tracking import get_ranges
from ddtrace.appsec._iast._taint_tracking import num_objects_tainted
from ddtrace.appsec._iast._taint_tracking import set_ranges
from ddtrace.appsec._iast._taint_tracking import shift_taint_range
from ddtrace.appsec._iast._taint_tracking import shift_taint_ranges
from ddtrace.appsec._iast._taint_tracking._context import create_context
from ddtrace.appsec._iast._taint_tracking._context import reset_context
from ddtrace.appsec._iast._taint_tracking._context import reset_contexts
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import VulnerabilityType
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import is_notinterned_notfasttainted_unicode
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import set_fast_tainted_if_notinterned_unicode
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import bytearray_extend_aspect as extend_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import format_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import join_aspect
from tests.appsec.iast.iast_utils import IAST_VALID_LOG
from tests.utils import override_env
from tests.utils import override_global_config


def test_source_origin_refcount():
    s1 = Source(name="name", value="val", origin=OriginType.COOKIE)
    assert sys.getrefcount(s1) - 1 == 1  # getrefcount takes 1 while counting
    s2 = s1
    assert sys.getrefcount(s1) - 1 == 2
    s3 = s1
    assert sys.getrefcount(s1) - 1 == 3
    del s2
    assert sys.getrefcount(s1) - 1 == 2
    # TaintRange does not increase refcount but should keep it alive
    tr_sub = TaintRange(0, 1, s1)
    assert sys.getrefcount(s1) - 1 == 2
    del s1
    assert sys.getrefcount(s3) - 1 == 1
    assert sys.getrefcount(tr_sub.source) - 1 == 1
    del s3
    assert sys.getrefcount(tr_sub.source) - 1 == 1
    _ = TaintRange(1, 2, tr_sub.source)
    assert sys.getrefcount(tr_sub.source) - 1 == 1


_SOURCE1 = Source(name="name", value="value", origin=OriginType.COOKIE)
_SOURCE2 = Source(name="name2", value="value2", origin=OriginType.BODY)

_RANGE1 = TaintRange(0, 2, _SOURCE1)
_RANGE2 = TaintRange(1, 3, _SOURCE2)


def test_unicode_fast_tainting():
    for i in range(5000):
        s = "somestr" * random.randint(4 * i + 7, 4 * i + 9)
        s_check = "somestr" * (4 * i + 10)
        # Check that s is not interned since fast tainting only works on non-interned strings
        assert s is not s_check
        assert is_notinterned_notfasttainted_unicode(s), "%s,%s" % (i, len(s) // 7)

        set_fast_tainted_if_notinterned_unicode(s)
        assert not is_notinterned_notfasttainted_unicode(s)

        b = b"foobar" * 4000
        assert not is_notinterned_notfasttainted_unicode(b)
        set_fast_tainted_if_notinterned_unicode(b)
        assert not is_notinterned_notfasttainted_unicode(b)

        ba = bytearray(b"sfdsdfsdf" * 4000)
        assert not is_notinterned_notfasttainted_unicode(ba)
        set_fast_tainted_if_notinterned_unicode(ba)
        assert not is_notinterned_notfasttainted_unicode(ba)

        c = 12345
        assert not is_notinterned_notfasttainted_unicode(c)
        set_fast_tainted_if_notinterned_unicode(c)
        assert not is_notinterned_notfasttainted_unicode(c)


def test_collisions_str():
    not_tainted = []
    tainted = []
    mixed_tainted_ids = []
    mixed_nottainted = []
    mixed_tainted_and_nottainted = []

    # Generate untainted strings
    for i in range(10):
        not_tainted.append("N%04d" % i)

    # Generate tainted strings
    for i in range(10000):
        t = taint_pyobject(
            "T%04d" % i, source_name="request_body", source_value="hello", source_origin=OriginType.PARAMETER
        )
        tainted.append(t)

    for t in tainted:
        assert len(get_ranges(t)) > 0

    for n in not_tainted:
        assert get_ranges(n) == []

    # Do join and format operations mixing tainted and untainted strings, store in mixed_tainted_and_nottainted
    for _ in range(10000):
        n1 = add_aspect(add_aspect("_", random.choice(not_tainted)), "{}_")

        t2 = random.choice(tainted)

        n3 = random.choice(not_tainted)

        t4 = random.choice(tainted)
        mixed_tainted_ids.append(id(t4))

        t5 = format_aspect(n1.format, 1, n1, t4)
        mixed_tainted_ids.append(id(t5))

        t6 = join_aspect(t5.join, 1, t5, [t2, n3])
        mixed_tainted_ids.append(id(t6))

    for t in mixed_tainted_and_nottainted:
        assert len(get_ranges(t)) == 2

    # Do join and format operations with only untainted strings, store in mixed_nottainted
    for i in range(10):
        n1 = add_aspect("===", not_tainted[i])

        n2 = random.choice(not_tainted)

        n3 = random.choice(not_tainted)

        n4 = random.choice(not_tainted)

        n5 = format_aspect(n1.format, 1, n1, [n4])

        n6 = join_aspect(n5.join, 1, n5, [n2, n3])

        mixed_nottainted.append(n6)

    for n in mixed_nottainted:
        assert len(get_ranges(n)) == 0, f"id {id(n)} in {(id(n) in mixed_tainted_ids)}"

    taint_map = literal_eval(debug_taint_map())
    assert taint_map != []
    for i in taint_map:
        assert abs(i["Value"]["Hash"]) > 1000


def test_collisions_bytes():
    not_tainted = []
    tainted = []
    mixed_tainted_ids = []
    mixed_nottainted = []
    mixed_tainted_and_nottainted = []

    # Generate untainted strings
    for i in range(10):
        not_tainted.append(b"N%04d" % i)

    # Generate tainted strings
    for i in range(10000):
        t = taint_pyobject(
            b"T%04d" % i, source_name="request_body", source_value="hello", source_origin=OriginType.PARAMETER
        )
        tainted.append(t)

    for t in tainted:
        assert len(get_ranges(t)) > 0

    for n in not_tainted:
        assert get_ranges(n) == []

    # Do join operations mixing tainted and untainted bytes, store in mixed_tainted_and_nottainted
    for _ in range(10000):
        n1 = add_aspect(add_aspect(b"_", random.choice(not_tainted)), b"{}_")

        t2 = random.choice(tainted)

        n3 = random.choice(not_tainted)

        t4 = random.choice(tainted)
        mixed_tainted_ids.append(id(t4))

        t5 = join_aspect(n1.join, 1, t2, [n1, t4])
        mixed_tainted_ids.append(id(t5))

        t6 = join_aspect(t5.join, 1, t5, [t2, n3])
        mixed_tainted_ids.append(id(t6))

    for t in mixed_tainted_and_nottainted:
        assert len(get_ranges(t)) == 2

    # Do join operations with only untainted bytes, store in mixed_nottainted
    for i in range(10):
        n1 = add_aspect(b"===", not_tainted[i])

        n2 = random.choice(not_tainted)

        n3 = random.choice(not_tainted)

        n4 = random.choice(not_tainted)

        n5 = join_aspect(n1.join, 1, n1, [n2, n4])

        n6 = join_aspect(n5.join, 1, n5, [n2, n3])

        mixed_nottainted.append(n6)

    for n in mixed_nottainted:
        assert len(get_ranges(n)) == 0, f"id {id(n)} in {(id(n) in mixed_tainted_ids)}"

    taint_map = literal_eval(debug_taint_map())
    assert taint_map != []
    for i in taint_map:
        assert abs(i["Value"]["Hash"]) > 1000


def test_collisions_bytearray():
    not_tainted = []
    tainted = []
    mixed_tainted_ids = []
    mixed_nottainted = []
    mixed_tainted_and_nottainted = []

    # Generate untainted strings
    for i in range(10):
        not_tainted.append(b"N%04d" % i)

    # Generate tainted strings
    for i in range(10000):
        t = taint_pyobject(
            b"T%04d" % i,
            source_name="request_body",
            source_value="hello",
            source_origin=OriginType.PARAMETER,
        )
        tainted.append(t)

    for t in tainted:
        assert len(get_ranges(t)) > 0

    for n in not_tainted:
        assert get_ranges(n) == []

    # Do extend operations mixing tainted and untainted bytearrays, store in mixed_tainted_and_nottainted
    for _ in range(10000):
        n1 = bytearray(add_aspect(b"_", random.choice(not_tainted)))
        extend_aspect(bytearray(b"").extend, 1, n1, b"{}_")

        n3 = random.choice(not_tainted)

        t4 = random.choice(tainted)

        t5 = bytearray(add_aspect(b"_", random.choice(not_tainted)))
        extend_aspect(bytearray(b"").extend, 1, t5, t4)
        mixed_tainted_ids.append(id(t5))

        t6 = bytearray(add_aspect(b"_", random.choice(not_tainted)))
        extend_aspect(bytearray(b"").extend, 1, t6, n3)
        mixed_tainted_ids.append(id(t6))

    for t in mixed_tainted_and_nottainted:
        assert len(get_ranges(t)) == 2

    # Do extend operations with only untainted bytearrays, store in mixed_nottainted
    for i in range(10):
        n1 = bytearray(not_tainted[i])
        extend_aspect(bytearray(b"").extend, 1, n1, b"===")
        mixed_nottainted.append(n1)

        n2 = random.choice(not_tainted)

        n4 = bytearray(random.choice(not_tainted))

        n5 = bytearray(not_tainted[i])
        extend_aspect(bytearray(b"").extend, 1, n5, n4)
        mixed_nottainted.append(n5)

        n6 = bytearray(not_tainted[i])
        extend_aspect(bytearray(b"").extend, 1, n6, n2)

        mixed_nottainted.append(n6)

    for n in mixed_nottainted:
        assert len(get_ranges(n)) == 0, f"id {id(n)} in {(id(n) in mixed_tainted_ids)}"

    taint_map = literal_eval(debug_taint_map())
    assert taint_map != []
    for i in taint_map:
        assert abs(i["Value"]["Hash"]) > 1000


def test_set_get_ranges_str():
    s1 = "abcde😁"
    s2 = "defg"
    set_ranges(s1, [_RANGE1, _RANGE2])
    assert get_ranges(s1) == [_RANGE1, _RANGE2]
    assert not get_ranges(s2)


def test_set_get_ranges_other():
    s1 = 12345
    s2 = None
    set_ranges(s1, [_RANGE1, _RANGE2])
    set_ranges(s2, [_RANGE1, _RANGE2])
    with pytest.raises(
        ValueError,
        match=re.escape("iast::propagation::native::error::Get ranges error: Invalid type of candidate_text variable"),
    ):
        get_ranges(s1)

    with pytest.raises(
        ValueError,
        match=re.escape("iast::propagation::native::error::Get ranges error: Invalid type of candidate_text variable"),
    ):
        get_ranges(s2)


def test_set_get_ranges_bytes():
    b1 = b"ABCDE"
    b2 = b"DEFG"
    set_ranges(b1, [_RANGE2, _RANGE1])
    assert get_ranges(b1) == [_RANGE2, _RANGE1]
    assert not get_ranges(b2) == [_RANGE2, _RANGE1]


def test_set_get_ranges_bytearray():
    b1 = bytearray(b"abcdef")
    b2 = bytearray(b"abcdef")
    set_ranges(b1, [_RANGE1, _RANGE2])
    assert get_ranges(b1) == [_RANGE1, _RANGE2]
    assert not get_ranges(b2) == [_RANGE1, _RANGE2]


@pytest.mark.parametrize(
    "source",
    [
        Source(name="name", value="value", origin=OriginType.PARAMETER_NAME),
        Source(name="name", value="value", origin=OriginType.PARAMETER),
        Source(name="name", value="value", origin=OriginType.HEADER_NAME),
        Source(name="name", value="value", origin=OriginType.COOKIE),
        Source(name="name", value="value", origin=OriginType.PATH),
    ],
)
@pytest.mark.parametrize(
    "vulnerability_type",
    [
        [],
        [VulnerabilityType.CODE_INJECTION],
        [VulnerabilityType.COMMAND_INJECTION],
        [VulnerabilityType.HEADER_INJECTION],
        [VulnerabilityType.NO_HTTPONLY_COOKIE],
        [VulnerabilityType.NO_SAMESITE_COOKIE],
        [VulnerabilityType.PATH_TRAVERSAL],
        [VulnerabilityType.SQL_INJECTION],
        [VulnerabilityType.SSRF],
        [VulnerabilityType.CODE_INJECTION, VulnerabilityType.COMMAND_INJECTION],
        [VulnerabilityType.CODE_INJECTION, VulnerabilityType.COMMAND_INJECTION, VulnerabilityType.HEADER_INJECTION],
        [
            VulnerabilityType.CODE_INJECTION,
            VulnerabilityType.COMMAND_INJECTION,
            VulnerabilityType.HEADER_INJECTION,
            VulnerabilityType.NO_HTTPONLY_COOKIE,
        ],
        [
            VulnerabilityType.CODE_INJECTION,
            VulnerabilityType.COMMAND_INJECTION,
            VulnerabilityType.HEADER_INJECTION,
            VulnerabilityType.NO_HTTPONLY_COOKIE,
            VulnerabilityType.NO_SAMESITE_COOKIE,
        ],
        [
            VulnerabilityType.CODE_INJECTION,
            VulnerabilityType.COMMAND_INJECTION,
            VulnerabilityType.HEADER_INJECTION,
            VulnerabilityType.NO_HTTPONLY_COOKIE,
            VulnerabilityType.NO_SAMESITE_COOKIE,
            VulnerabilityType.PATH_TRAVERSAL,
        ],
        [
            VulnerabilityType.CODE_INJECTION,
            VulnerabilityType.COMMAND_INJECTION,
            VulnerabilityType.HEADER_INJECTION,
            VulnerabilityType.NO_HTTPONLY_COOKIE,
            VulnerabilityType.NO_SAMESITE_COOKIE,
            VulnerabilityType.PATH_TRAVERSAL,
            VulnerabilityType.SQL_INJECTION,
        ],
        [
            VulnerabilityType.CODE_INJECTION,
            VulnerabilityType.COMMAND_INJECTION,
            VulnerabilityType.HEADER_INJECTION,
            VulnerabilityType.NO_HTTPONLY_COOKIE,
            VulnerabilityType.NO_SAMESITE_COOKIE,
            VulnerabilityType.PATH_TRAVERSAL,
            VulnerabilityType.SQL_INJECTION,
            VulnerabilityType.SSRF,
        ],
    ],
)
def test_shift_taint_ranges(source, vulnerability_type):
    r1 = TaintRange(0, 2, source, vulnerability_type)
    r1_shifted = shift_taint_range(r1, 2)
    assert r1_shifted == TaintRange(2, 2, source, vulnerability_type)
    assert r1_shifted != r1

    r2 = TaintRange(1, 3, _SOURCE1)
    r3 = TaintRange(4, 6, _SOURCE2)
    r2_shifted, r3_shifted = shift_taint_ranges([r2, r3], 2)
    assert r2_shifted == TaintRange(3, 3, source, vulnerability_type)
    assert r3_shifted == TaintRange(6, 6, source, vulnerability_type)


def test_are_all_text_all_ranges():
    s1 = "abcdef"
    s2 = "ghijk"
    s3 = "xyzv"
    num = 123456
    source3 = Source(name="name3", value="value3", origin=OriginType.COOKIE)
    source4 = Source(name="name4", value="value4", origin=OriginType.COOKIE)
    range3 = TaintRange(2, 3, source3)
    range4 = TaintRange(4, 5, source4)
    set_ranges(s1, [_RANGE1, _RANGE2])
    set_ranges(s2, [range3, _RANGE2])
    set_ranges(s3, [range4, _RANGE1])
    all_ranges, candidate_ranges = are_all_text_all_ranges(s1, (s2, s3, num))
    # Ranges are inserted at the start except the candidate ones that are appended
    assert all_ranges == [range3, _RANGE2, range4, _RANGE1, _RANGE1, _RANGE2]
    assert candidate_ranges == [_RANGE1, _RANGE2]


def test_get_range_by_hash():
    hash_r1 = hash(_RANGE1)
    assert hash_r1 == _RANGE1.__hash__()
    hash_r2_call = hash(_RANGE2)
    hash_r2_method = _RANGE2.__hash__()
    assert hash_r2_call == hash_r2_method
    assert hash_r1 != hash_r2_call
    assert get_range_by_hash(hash_r1, [_RANGE1, _RANGE2]) == _RANGE1
    assert get_range_by_hash(hash_r2_call, [_RANGE1, _RANGE2]) == _RANGE2


def test_num_objects_tainted():
    create_context()
    a_1 = "abc123_len1"
    a_2 = "def456__len2"
    a_3 = "ghi789___len3"
    assert num_objects_tainted() == 0
    a_1 = taint_pyobject(
        a_1,
        source_name="test_num_objects_tainted",
        source_value=a_1,
        source_origin=OriginType.PARAMETER,
    )
    a_2 = taint_pyobject(
        a_2,
        source_name="test_num_objects_tainted",
        source_value=a_2,
        source_origin=OriginType.PARAMETER,
    )
    a_3 = taint_pyobject(
        a_3,
        source_name="test_num_objects_tainted",
        source_value=a_3,
        source_origin=OriginType.PARAMETER,
    )
    assert num_objects_tainted() == 3


def test_reset_objects():
    create_context()

    a_1 = "abc123"
    a_2 = "def456"
    assert num_objects_tainted() == 0
    a_1 = taint_pyobject(
        a_1,
        source_name="test_num_objects_tainted",
        source_value=a_1,
        source_origin=OriginType.PARAMETER,
    )
    assert num_objects_tainted() == 1

    reset_context()
    create_context()
    a_2 = taint_pyobject(
        a_2,
        source_name="test_num_objects_tainted",
        source_value=a_2,
        source_origin=OriginType.PARAMETER,
    )
    assert num_objects_tainted() == 1

    reset_context()
    create_context()

    assert num_objects_tainted() == 0

    reset_context()


def reset_context_loop():
    a_1 = "abc123"
    for i in range(25):
        a_1 = taint_pyobject(
            a_1,
            source_name="test_num_objects_tainted",
            source_value=a_1,
            source_origin=OriginType.PARAMETER,
        )
        sleep(0.01)


def reset_contexts_loop():
    create_context()

    a_1 = "abc123"
    for i in range(25):
        a_1 = taint_pyobject(
            a_1,
            source_name="test_num_objects_tainted",
            source_value=a_1,
            source_origin=OriginType.PARAMETER,
        )
        sleep(0.01)
    reset_contexts()


async def async_reset_context_loop(task_id: int):
    await asyncio.sleep(0.01)
    return reset_context_loop()


async def async_reset_contexts_loop(task_id: int):
    await asyncio.sleep(0.01)
    return reset_contexts_loop()


def test_race_conditions_threads(caplog, telemetry_writer):
    """we want to validate context is working correctly among multiple request and no race condition creating and
    destroying contexts
    """
    pool = ThreadPool(processes=3)
    results_async = [pool.apply_async(reset_context_loop) for _ in range(70)]
    _ = [res.get() for res in results_async]

    log_messages = [record.message for record in caplog.get_records("call")]
    for message in log_messages:
        if IAST_VALID_LOG.search(message):
            pytest.fail(message)

    list_metrics_logs = list(telemetry_writer._logs)
    assert len(list_metrics_logs) == 0


@pytest.mark.skip_iast_check_logs
def test_race_conditions_reset_contexts_threads(caplog, telemetry_writer):
    """we want to validate context is working correctly among multiple request and no race condition creating and
    destroying contexts
    """
    with override_env({"_DD_IAST_USE_ROOT_SPAN": "false"}), override_global_config(
        dict(_iast_debug=True)
    ), caplog.at_level(logging.DEBUG):
        pool = ThreadPool(processes=3)
        results_async = [pool.apply_async(reset_contexts_loop) for _ in range(70)]
        _ = [res.get() for res in results_async]

        log_messages = [record.message for record in caplog.get_records("call")]
        if not any(IAST_VALID_LOG.search(message) for message in log_messages):
            pytest.fail("All contexts reset but no fail")
        list_metrics_logs = list(telemetry_writer._logs)
        assert len(list_metrics_logs) == 0


@pytest.mark.asyncio
async def test_race_conditions_reset_contex_async(caplog, telemetry_writer):
    """we want to validate context is working correctly among multiple request and no race condition creating and
    destroying contexts
    """
    tasks = [async_reset_context_loop(i) for i in range(50)]

    results = await asyncio.gather(*tasks)
    assert results

    log_messages = [record.message for record in caplog.get_records("call")]
    for message in log_messages:
        if IAST_VALID_LOG.search(message):
            pytest.fail(message)

    list_metrics_logs = list(telemetry_writer._logs)
    assert len(list_metrics_logs) == 0


@pytest.mark.asyncio
async def test_race_conditions_reset_contexs_async(caplog, telemetry_writer):
    """we want to validate context is working correctly among multiple request and no race condition creating and
    destroying contexts
    """
    tasks = [async_reset_contexts_loop(i) for i in range(20)]

    results = await asyncio.gather(*tasks)
    assert results

    log_messages = [record.message for record in caplog.get_records("call")]
    for message in log_messages:
        if IAST_VALID_LOG.search(message):
            pytest.fail(message)

    list_metrics_logs = list(telemetry_writer._logs)
    assert len(list_metrics_logs) == 0


@pytest.mark.parametrize(
    "source_origin",
    [
        OriginType.PARAMETER_NAME,
        OriginType.PARAMETER,
        OriginType.HEADER_NAME,
        OriginType.COOKIE,
        OriginType.BODY,
        OriginType.PATH,
    ],
)
def test_has_origin_match(source_origin):
    """Test that has_origin correctly identifies matching origins."""
    source = Source(name="name", value="value", origin=source_origin)
    taint_range = TaintRange(0, 2, source)
    assert taint_range.has_origin(source_origin)


@pytest.mark.parametrize(
    "source_origin,test_origin",
    [
        (OriginType.PARAMETER_NAME, OriginType.PARAMETER),
        (OriginType.PARAMETER, OriginType.COOKIE),
        (OriginType.HEADER_NAME, OriginType.BODY),
        (OriginType.COOKIE, OriginType.PATH),
        (OriginType.BODY, OriginType.PARAMETER_NAME),
        (OriginType.PATH, OriginType.HEADER_NAME),
    ],
)
def test_has_origin_no_match(source_origin, test_origin):
    """Test that has_origin correctly identifies non-matching origins."""
    source = Source(name="name", value="value", origin=source_origin)
    taint_range = TaintRange(0, 2, source)
    assert not taint_range.has_origin(test_origin)


def test_has_origin_multiple_ranges():
    """Test has_origin with multiple taint ranges."""
    source1 = Source(name="name1", value="value1", origin=OriginType.COOKIE)
    source2 = Source(name="name2", value="value2", origin=OriginType.PARAMETER)

    taint_range1 = TaintRange(0, 2, source1)
    taint_range2 = TaintRange(2, 2, source2)

    assert taint_range1.has_origin(OriginType.COOKIE)
    assert not taint_range1.has_origin(OriginType.PARAMETER)
    assert taint_range2.has_origin(OriginType.PARAMETER)
    assert not taint_range2.has_origin(OriginType.COOKIE)


def test_has_origin_invalid_origin():
    """Test has_origin with invalid origin type."""
    source = Source(name="name", value="value", origin=OriginType.COOKIE)
    taint_range = TaintRange(0, 2, source)
    with pytest.raises(TypeError):
        taint_range.has_origin("INVALID_ORIGIN")
