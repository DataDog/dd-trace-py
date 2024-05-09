import grpc
from grpc._grpcio_metadata import __version__ as _GRPC_VERSION
import mock

from tests.contrib.grpc.common import GrpcBaseTestCase
from tests.contrib.grpc.hello_pb2 import HelloRequest
from tests.contrib.grpc.hello_pb2_grpc import HelloStub
from tests.utils import TracerTestCase
from tests.utils import override_env


_GRPC_PORT = 50531
_GRPC_VERSION = tuple([int(i) for i in _GRPC_VERSION.split(".")])


def _check_test_range(value):
    from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges

    ranges = get_tainted_ranges(value)
    assert len(ranges) == 1
    source = ranges[0].source
    assert source.name == "http.request.grpc_body"
    assert hasattr(source, "value")


class GrpcTestIASTCase(GrpcBaseTestCase):
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_IAST_ENABLED="1"))
    def test_taint_iast_single(self):
        with override_env({"DD_IAST_ENABLED": "True"}):
            with self.override_config("grpc", dict(service_name="myclientsvc")):
                with self.override_config("grpc_server", dict(service_name="myserversvc")):
                    channel1 = grpc.insecure_channel("localhost:%d" % (_GRPC_PORT))
                    stub1 = HelloStub(channel1)
                    res = stub1.SayHello(HelloRequest(name="test"))
                    assert hasattr(res, "message")
                    _check_test_range(res.message)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_IAST_ENABLED="1"))
    def test_taint_iast_twice(self):
        with override_env({"DD_IAST_ENABLED": "True"}):
            with self.override_config("grpc", dict(service_name="myclientsvc")):
                with self.override_config("grpc_server", dict(service_name="myserversvc")):
                    channel1 = grpc.insecure_channel("localhost:%d" % (_GRPC_PORT))
                    stub1 = HelloStub(channel1)
                    responses_iterator = stub1.SayHelloTwice(HelloRequest(name="test"))
                    for res in responses_iterator:
                        assert hasattr(res, "message")
                        _check_test_range(res.message)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_IAST_ENABLED="1"))
    def test_taint_iast_repeatedly(self):
        with override_env({"DD_IAST_ENABLED": "True"}):
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

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_IAST_ENABLED="1"))
    def test_taint_iast_last(self):
        with override_env({"DD_IAST_ENABLED": "True"}):
            with self.override_config("grpc", dict(service_name="myclientsvc")):
                with self.override_config("grpc_server", dict(service_name="myserversvc")):
                    channel1 = grpc.insecure_channel("localhost:%d" % (_GRPC_PORT))
                    stub1 = HelloStub(channel1)
                    requests_iterator = iter(HelloRequest(name=name) for name in ["first", "second"])
                    res = stub1.SayHelloLast(requests_iterator)
                    assert hasattr(res, "message")
                    _check_test_range(res.message)

    def test_taint_iast_patching_import_error(self):
        with mock.patch.dict("sys.modules", {"google._upb._message": None}), override_env({"DD_IAST_ENABLED": "True"}):
            from collections import UserDict

            from ddtrace.appsec._handlers import _custom_protobuf_getattribute
            from ddtrace.appsec._handlers import _patch_protobuf_class

            class MyUserDict(UserDict):
                pass

            _patch_protobuf_class(MyUserDict)
            original_dict = {"apple": 1, "banana": 2}
            mutable_mapping = MyUserDict(original_dict)

            _custom_protobuf_getattribute(mutable_mapping, "data")
