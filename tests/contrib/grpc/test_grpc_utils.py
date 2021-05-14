import mock
import pytest

from ddtrace.contrib.grpc.utils import _parse_target_from_args
from ddtrace.contrib.grpc.utils import parse_method_path


def test_parse_method_path_with_package():
    method_path = "/package.service/method"
    parsed = parse_method_path(method_path)
    assert parsed == ("package", "service", "method")


def test_parse_method_path_without_package():
    method_path = "/service/method"
    parsed = parse_method_path(method_path)
    assert parsed == (None, "service", "method")


@mock.patch("ddtrace.contrib.grpc.utils.log")
@pytest.mark.parametrize(
    "args, kwargs, result, log_warning_call",
    [
        (("localhost:1234",), dict(), ("localhost", 1234), None),
        (("localhost",), dict(), ("localhost", None), None),
        (("[::]:1234",), dict(), ("::", 1234), None),
        (("[::]",), dict(), ("::", None), None),
        (None, dict(target="localhost:1234"), ("localhost", 1234), None),
        (None, dict(target="localhost"), ("localhost", None), None),
        (None, dict(target="[::]:1234"), ("::", 1234), None),
        (None, dict(target="[::]"), ("::", None), None),
        (("localhost:foo",), dict(), ("localhost", None), ("Non-integer port in target '%s'", "localhost:foo")),
        (("",), dict(), (None, None), None),
    ],
)
def test_parse_target_from_args(mock_log, args, kwargs, result, log_warning_call):
    assert _parse_target_from_args(args, kwargs) == result
    if log_warning_call:
        mock_log.warning.assert_called_once_with(*log_warning_call)
    else:
        mock_log.warning.assert_not_called()
