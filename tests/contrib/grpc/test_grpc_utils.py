import mock
import pytest

from ddtrace._trace.span import Span
from ddtrace.contrib.internal.grpc import utils


def test_parse_method_path_with_package():
    method_path = "/package.service/method"
    parsed = utils.parse_method_path(method_path)
    assert parsed == ("package.service", "package", "service", "method")


def test_parse_method_path_without_package():
    method_path = "/service/method"
    parsed = utils.parse_method_path(method_path)
    assert parsed == ("service", None, "service", "method")


@mock.patch("ddtrace.contrib.internal.grpc.utils.log")
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
    "host, port, expected_tags",
    [
        (
            "localhost",
            1234,
            {
                "grpc.host": "localhost",
                "peer.hostname": "localhost",
                "network.destination.port": "1234",
                "span.kind": "client",
            },
        ),
        (
            "localhost",
            None,
            {
                "grpc.host": "localhost",
                "peer.hostname": "localhost",
                "span.kind": "client",
            },
        ),
        (
            "127.0.0.1",
            1234,
            {
                "grpc.host": "127.0.0.1",
                "network.destination.ip": "127.0.0.1",
                "network.destination.port": "1234",
                "span.kind": "client",
            },
        ),
        (
            "::1",
            1234,
            {
                "grpc.host": "::1",
                "network.destination.ip": "::1",
                "network.destination.port": "1234",
                "span.kind": "client",
            },
        ),
        (None, 1234, {"network.destination.port": "1234", "span.kind": "client"}),
        (None, None, {"span.kind": "client"}),
    ],
)
def test_set_grpc_client_meta(host, port, expected_tags):
    span = Span("test")
    utils.set_grpc_client_meta(span, host, port)
    for key, value in expected_tags.items():
        assert span._get_attribute(key) == value, f"Expected tag {key}={value!r}, got {span._get_attribute(key)!r}"


@pytest.mark.parametrize(
    "method, method_kind, expected_tags",
    [
        (
            "/package.service/method",
            "unary",
            {
                "rpc.service": "package.service",
                "grpc.method.path": "/package.service/method",
                "grpc.method.package": "package",
                "grpc.method.service": "service",
                "grpc.method.name": "method",
                "grpc.method.kind": "unary",
            },
        ),
        (
            "/service/method",
            "unary",
            {
                "rpc.service": "service",
                "grpc.method.path": "/service/method",
                "grpc.method.service": "service",
                "grpc.method.name": "method",
                "grpc.method.kind": "unary",
            },
        ),
    ],
)
def test_set_grpc_method_meta(method, method_kind, expected_tags):
    span = Span("test")
    utils.set_grpc_method_meta(span, method, method_kind)
    for key, value in expected_tags.items():
        assert span._get_attribute(key) == value, f"Expected tag {key}={value!r}, got {span._get_attribute(key)!r}"
