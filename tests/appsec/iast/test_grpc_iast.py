import threading
from unittest import mock

import grpc
from grpc._grpcio_metadata import __version__ as _GRPC_VERSION
import pytest

from ddtrace.appsec._constants import SPAN_DATA_NAMES
from tests.contrib.grpc.common import GrpcBaseTestCase
from tests.contrib.grpc.hello_pb2 import HelloRequest
from tests.contrib.grpc.hello_pb2_grpc import HelloStub
from tests.utils import override_config
from tests.utils import override_env

from .conftest import iast_context


_GRPC_PORT = 50531
_GRPC_VERSION = tuple([int(i) for i in _GRPC_VERSION.split(".")])


@pytest.fixture(autouse=True)
def iast_c_context():
    yield from iast_context(dict(DD_IAST_ENABLED="true"), asm_enabled=True)


def _check_test_range(value):
    from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges

    ranges = get_tainted_ranges(value)
    assert len(ranges) == 1, f"found {len(ranges)} ranges"
    source = ranges[0].source
    assert source.name == "http.request.grpc_body"
    assert hasattr(source, "value")


class GrpcTestIASTCase(GrpcBaseTestCase):
    def test_taint_iast_single(self):
        with self.override_config("grpc", dict(service_name="myclientsvc")):
            with self.override_config("grpc_server", dict(service_name="myserversvc")):
                channel1 = grpc.insecure_channel("localhost:%d" % (_GRPC_PORT))
                stub1 = HelloStub(channel1)
                res = stub1.SayHello(HelloRequest(name="test"))
                assert hasattr(res, "message")
                _check_test_range(res.message)

    def test_taint_iast_single_server(self):
        with grpc.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel1:
            stub1 = HelloStub(channel1)
            res = stub1.SayHello(HelloRequest(name="test"))
            assert hasattr(res, "message")
            _check_test_range(res.message)

    def test_taint_iast_twice(self):
        with self.override_config("grpc", dict(service_name="myclientsvc")):
            with self.override_config("grpc_server", dict(service_name="myserversvc")):
                with grpc.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel1:
                    stub1 = HelloStub(channel1)
                    responses_iterator = stub1.SayHelloTwice(HelloRequest(name="test"))
                    for res in responses_iterator:
                        assert hasattr(res, "message")
                        _check_test_range(res.message)

    def test_taint_iast_twice_server(self):
        # use an event to signal when the callbacks have been called from the response
        callback_called = threading.Event()

        def callback(response):
            callback_called.set()

        with grpc.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel1:
            stub1 = HelloStub(channel1)
            responses_iterator = stub1.SayHelloTwice(HelloRequest(name="test"))
            responses_iterator.add_done_callback(callback)
            for res in responses_iterator:
                assert hasattr(res, "message")
                _check_test_range(res.message)

            callback_called.wait(timeout=1)

    def test_taint_iast_repeatedly(self):
        with self.override_config("grpc", dict(service_name="myclientsvc")):
            with self.override_config("grpc_server", dict(service_name="myserversvc")):
                channel1 = grpc.insecure_channel("localhost:%d" % (_GRPC_PORT))
                stub1 = HelloStub(channel1)
                requests_iterator = iter(
                    HelloRequest(name=name) for name in ["first", "second", "third", "fourth", "fifth"]
                )
                responses_iterator = stub1.SayHelloRepeatedly(requests_iterator)
                for res in responses_iterator:
                    assert hasattr(res, "message")
                    _check_test_range(res.message)

    def test_taint_iast_repeatedly_server(self):
        # use an event to signal when the callbacks have been called from the response
        callback_called = threading.Event()

        def callback(response):
            callback_called.set()

        with grpc.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel1:
            stub1 = HelloStub(channel1)
            requests_iterator = iter(
                HelloRequest(name=name) for name in ["first", "second", "third", "fourth", "fifth"]
            )
            responses_iterator = stub1.SayHelloRepeatedly(requests_iterator)
            responses_iterator.add_done_callback(callback)
            for res in responses_iterator:
                assert hasattr(res, "message")
                _check_test_range(res.message)

            callback_called.wait(timeout=1)

    def test_taint_iast_last(self):
        with self.override_config("grpc", dict(service_name="myclientsvc")):
            with self.override_config("grpc_server", dict(service_name="myserversvc")):
                channel1 = grpc.insecure_channel("localhost:%d" % (_GRPC_PORT))
                stub1 = HelloStub(channel1)
                requests_iterator = iter(HelloRequest(name=name) for name in ["first", "second"])
                res = stub1.SayHelloLast(requests_iterator)
                assert hasattr(res, "message")
                _check_test_range(res.message)

    def test_taint_iast_last_server(self):
        with grpc.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel1:
            stub1 = HelloStub(channel1)
            requests_iterator = iter(HelloRequest(name=name) for name in ["first", "second"])
            res = stub1.SayHelloLast(requests_iterator)
            assert hasattr(res, "message")
            _check_test_range(res.message)

    def test_taint_iast_patching_import_error(self):
        with mock.patch.dict("sys.modules", {"google._upb._message": None}), override_env({"DD_IAST_ENABLED": "True"}):
            from collections import UserDict

            from ddtrace.appsec._iast._handlers import _custom_protobuf_getattribute
            from ddtrace.appsec._iast._handlers import _patch_protobuf_class

            class MyUserDict(UserDict):
                pass

            _patch_protobuf_class(MyUserDict)
            original_dict = {"apple": 1, "banana": 2}
            mutable_mapping = MyUserDict(original_dict)

            _custom_protobuf_getattribute(mutable_mapping, "data")

    def test_address_server_data(self):
        with override_config("grpc", dict(service_name="myclientsvc")), override_config(
            "grpc_server", dict(service_name="myserversvc")
        ):
            with mock.patch("ddtrace.appsec._asm_request_context.set_waf_address") as mock_set_waf_addr:
                channel1 = grpc.insecure_channel("localhost:%d" % (_GRPC_PORT))
                stub1 = HelloStub(channel1)
                res = stub1.SayHello(HelloRequest(name="test"))
                assert hasattr(res, "message")
                mock_set_waf_addr.assert_any_call(SPAN_DATA_NAMES.GRPC_SERVER_RESPONSE_MESSAGE, mock.ANY)
                mock_set_waf_addr.assert_any_call(SPAN_DATA_NAMES.GRPC_SERVER_REQUEST_METADATA, mock.ANY)
                mock_set_waf_addr.assert_any_call(SPAN_DATA_NAMES.GRPC_SERVER_METHOD, "/helloworld.Hello/SayHello")
