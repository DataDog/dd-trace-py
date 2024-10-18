#!/usr/bin/env python3
import pytest

from ddtrace.appsec._common_module_patches import patch_common_modules
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import stringio_aspect
from tests.utils import override_global_config


def test_stringio_aspect_read():
    with override_global_config(dict(_iast_enabled=True)):
        patch_common_modules()
        tainted = taint_pyobject(
            pyobject="foobar",
            source_name="test_stringio_read_aspect_tainted_string",
            source_value="foobar",
            source_origin=OriginType.PARAMETER,
        )
        sio = stringio_aspect(None, 0, tainted)
        val = sio.read()
        assert is_pyobject_tainted(val)
        ranges = get_tainted_ranges(val)
        assert len(ranges) == 1


@pytest.mark.skip("TODO: APPSEC-55319")
def test_stringio_aspect_read_with_offset():
    with override_global_config(dict(_iast_enabled=True)):
        patch_common_modules()
        not_tainted = "foobazbazfoo"
        tainted = taint_pyobject(
            pyobject="foobar",
            source_name="test_stringio_read_aspect_tainted_string",
            source_value="foobar",
            source_origin=OriginType.PARAMETER,
        )
        added = add_aspect(not_tainted, tainted)
        sio = stringio_aspect(None, 0, added)
        val = sio.read(10)
        # If the StringIO() and read() aspects were perfect, `val` would not be tainted
        assert not is_pyobject_tainted(val)
        ranges = get_tainted_ranges(val)
        assert len(ranges) == 0

        val_tainted = sio.read(5)
        assert is_pyobject_tainted(val_tainted)
        ranges = get_tainted_ranges(val_tainted)
        assert len(ranges) == 1
