import mock
import pytest

from ddtrace.contrib.grpc import utils


def test_parse_method_path_with_package():
    method_path = "/package.service/method"
    parsed = utils.parse_method_path(method_path)
    assert parsed == ("package", "service", "method")


def test_parse_method_path_without_package():
    method_path = "/service/method"
    parsed = utils.parse_method_path(method_path)
    assert parsed == (None, "service", "method")


@mock.patch("ddtrace.contrib.grpc.utils.log")
@pytest.mark.parametrize(
    "args, kwargs, result, log_warning_call",
    [
        (("localhost:1234",), dict(), ("localhost", 1234), None),
        (("localhost",), dict(), ("localhost", None), None),
        ((":1234",), dict(), (None, 1234), None),
        (("[::]:1234",), dict(), ("::", 1234), None),
        (("[::]",), dict(), ("::", None), None),
        (None, dict(target="localhost:1234"), ("localhost", 1234), None),
        (None, dict(target="localhost"), ("localhost", None), None),
        (None, dict(target=":1234"), (None, 1234), None),
        (None, dict(target="[::]:1234"), ("::", 1234), None),
        (None, dict(target="[::]"), ("::", None), None),
        (("localhost:foo",), dict(), ("localhost", None), ("Non-integer port in target '%s'", "localhost:foo")),
        (("",), dict(), (None, None), None),
    ],
)
def test_parse_target_from_args(mock_log, args, kwargs, result, log_warning_call):
    assert utils._parse_target_from_args(args, kwargs) == result
    if log_warning_call:
        mock_log.warning.assert_called_once_with(*log_warning_call)
    else:
        mock_log.warning.assert_not_called()


@pytest.mark.parametrize(
    "host, port, calls",
    [
        (
            "localhost",
            1234,
            [mock.call("grpc.host", "localhost"), mock.call("grpc.port", "1234"), mock.call("span.kind", "client")],
        ),
        ("localhost", None, [mock.call("grpc.host", "localhost"), mock.call("span.kind", "client")]),
        (None, 1234, [mock.call("grpc.port", "1234"), mock.call("span.kind", "client")]),
        (None, None, [mock.call("span.kind", "client")]),
    ],
)
def test_set_grpc_client_meta(host, port, calls):
    span = mock.MagicMock()
    utils.set_grpc_client_meta(span, host, port)
    span.set_tag_str.assert_has_calls(calls)


@pytest.mark.parametrize(
    "method, method_kind, calls",
    [
        (
            "/package.service/method",
            "unary",
            [
                mock.call("grpc.method.path", "/package.service/method"),
                mock.call("grpc.method.package", "package"),
                mock.call("grpc.method.service", "service"),
                mock.call("grpc.method.name", "method"),
                mock.call("grpc.method.kind", "unary"),
            ],
        ),
        (
            "/service/method",
            "unary",
            [
                mock.call("grpc.method.path", "/service/method"),
                mock.call("grpc.method.service", "service"),
                mock.call("grpc.method.name", "method"),
                mock.call("grpc.method.kind", "unary"),
            ],
        ),
    ],
)
def test_set_grpc_method_meta(method, method_kind, calls):
    span = mock.MagicMock()
    utils.set_grpc_method_meta(span, method, method_kind)
    span.set_tag_str.assert_has_calls(calls)
