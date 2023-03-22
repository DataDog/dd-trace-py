#!/usr/bin/env python3
import threading

import pytest


# import time

try:
    from ddtrace.appsec.iast._input_info import Input_info
    from ddtrace.appsec.iast._taint_tracking import clear_taint_mapping  # type: ignore[attr-defined]
    from ddtrace.appsec.iast._taint_tracking import clear_taint_mapping_thread  # type: ignore[attr-defined]
    from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted  # type: ignore[attr-defined]
    from ddtrace.appsec.iast._taint_tracking import setup as taint_tracking_setup  # type: ignore[attr-defined]
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject  # type: ignore[attr-defined]
    from ddtrace.appsec.iast._taint_utils import LazyTaintDict

except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)


def setup():
    taint_tracking_setup(bytes.join, bytearray.join)
    clear_taint_mapping()


tainted_arg = None


def _in_second_thread(tainted_knights, main_thread_id):
    second_thread_id = threading.current_thread().ident  # get_native_id()
    assert second_thread_id != main_thread_id
    for v in tainted_knights.values():
        assert is_pyobject_tainted(v, second_thread_id)

    arg = "We interrupt this program to annoy you and make things generally more irritating."
    global tainted_arg
    tainted_arg = taint_pyobject(
        arg,
        Input_info("request_body", arg, 0),
        second_thread_id,
    )
    assert is_pyobject_tainted(tainted_arg, second_thread_id)

    clear_taint_mapping_thread(second_thread_id)
    assert not is_pyobject_tainted(tainted_arg, second_thread_id)


def test_two_threads():
    main_thread_id = threading.current_thread().ident  # get_native_id()

    tainted_knights = LazyTaintDict({"gallahad": "".join(("the pure", "")), "robin": "".join(("the brave", ""))})

    for v in tainted_knights.values():
        assert is_pyobject_tainted(v, main_thread_id)

    t = threading.Thread(target=_in_second_thread, args=(tainted_knights, main_thread_id))
    t.start()

    scratch = "Tis but a scratch."
    tainted_scratch = taint_pyobject(scratch, Input_info("request_body", scratch, 0), main_thread_id)

    global tainted_arg
    for v in tainted_knights.values():
        assert is_pyobject_tainted(v, main_thread_id)

    t.join()
    assert tainted_arg is not None
    assert not is_pyobject_tainted(tainted_arg, main_thread_id)

    clear_taint_mapping_thread(main_thread_id)

    # time.sleep(2)

    assert not is_pyobject_tainted(tainted_scratch, main_thread_id)

    for v in tainted_knights.values():
        assert is_pyobject_tainted(v, main_thread_id)
