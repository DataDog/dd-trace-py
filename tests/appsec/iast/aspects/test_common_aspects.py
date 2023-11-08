# -*- coding: utf-8 -*-
"""
Common tests to aspects, like ensuring that they don't break when receiving extra arguments.
"""
import os
import shutil

import pytest


try:
    from ddtrace.appsec._iast._taint_tracking import TagMappingMode  # noqa: F401
except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)


from tests.appsec.iast.aspects.conftest import _iast_patched_module
import tests.appsec.iast.fixtures.aspects.callees


def generate_callers_from_callees(callees_module, callers_file="", callees_module_str=""):
    module_functions = [x for x in dir(callees_module) if not x.startswith(("_", "@"))]

    with open(callers_file, "w") as callers:
        callers.write(f"from {callees_module_str} import *\n")
        callers.write(f"import {callees_module_str} as _original_callees\n")

        for function in module_functions:
            callers.write(
                f"""
def callee_{function}(*args, **kwargs):
    return _original_callees.{function}(*args, **kwargs)

def callee_{function}_direct(*args, **kwargs):
    return {function}(*args, **kwargs)\n
            """
            )


generate_callers_from_callees(
    callers_file="tests/appsec/iast/fixtures/aspects/callers.py",
    callees_module=tests.appsec.iast.fixtures.aspects.callees,
    callees_module_str="tests.appsec.iast.fixtures.aspects.callees",
)

patched_callers = _iast_patched_module("tests.appsec.iast.fixtures.aspects.callers")

## Contents of file tests/appsec/iast/fixtures/aspects/callers.py is inserted into
## tests/appsec/iast/fixtures/aspects/unpatched_callers.py to avoid patched import of the module

# Source file path
SOURCE_FILE = "tests/appsec/iast/fixtures/aspects/callers.py"

# File copy destination path
DESTINATION_FILE = "tests/appsec/iast/fixtures/aspects/unpatched_callers.py"

# Copy the file to the destination file
shutil.copy2(SOURCE_FILE, DESTINATION_FILE)

# This import needs to be done after the file is created (previous line)
from tests.appsec.iast.fixtures.aspects import unpatched_callers  # noqa: E402


@pytest.mark.parametrize("aspect", [x for x in dir(unpatched_callers) if not x.startswith(("_", "@"))])
@pytest.mark.parametrize("args", [(), ("a"), ("a", "b")])
@pytest.mark.parametrize("kwargs", [{}, {"dry_run": False}, {"dry_run": True}])
def test_aspect_patched_result(aspect, args, kwargs):
    assert getattr(patched_callers, aspect)(*args, **kwargs) == getattr(unpatched_callers, aspect)(*args, **kwargs)


os.remove(DESTINATION_FILE)
os.remove(SOURCE_FILE)
