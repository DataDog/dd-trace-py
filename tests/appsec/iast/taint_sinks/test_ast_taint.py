#!/usr/bin/env python3
import io
from unittest import mock

import pytest

from ddtrace.appsec._iast.taint_sinks.ast_taint import ast_function
from ddtrace.internal.telemetry import telemetry_writer


class MyArray:
    def __init__(self, values):
        self.values = values

    def copy(self):
        return self.values.copy()

    def __bool__(self):
        if len(self.values) > 0:
            raise ValueError("Array is not empty")
        return False


def test_ast_function_with_valueerror_on_bool():
    values = MyArray([7, 19, 20, 35, 10, 42, 8])
    values_copy = ast_function(values.copy, False)
    assert values_copy == values.values


def test_application_open_error_through_ast_function_is_not_reported_as_ddtrace_error(tmp_path):
    with pytest.raises(FileNotFoundError) as exc_info:
        ast_function(io.open, 0, str(tmp_path / "missing"))
    exc = exc_info.value

    with mock.patch.object(telemetry_writer, "add_error_log") as add_error_log:
        with mock.patch.object(telemetry_writer, "app_shutdown") as app_shutdown:
            telemetry_writer._telemetry_excepthook(type(exc), exc, exc.__traceback__)

    add_error_log.assert_not_called()
    app_shutdown.assert_called_once()
