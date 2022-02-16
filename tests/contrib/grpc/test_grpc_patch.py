from ddtrace.contrib.grpc import patch
from tests.contrib.patch import PatchTestCase


class TestGRPCPatch(PatchTestCase.Base):
    __integration_name__ = "grpc"
    __module_name__ = "grpc"
    __patch_func__ = patch
    __unpatch_func__ = None

    def assert_module_patched(self, grpc):
        # Client Wrapping
        self.assert_wrapped(grpc.insecure_channel)
        self.assert_wrapped(grpc.secure_channel)
        self.assert_wrapped(grpc.intercept_channel)
        # Server Wrapping
        self.assert_wrapped(grpc.server)

    def assert_not_module_patched(self, grpc):
        # Client Wrapping
        self.assert_not_wrapped(grpc.insecure_channel)
        self.assert_not_wrapped(grpc.secure_channel)
        self.assert_not_wrapped(grpc.intercept_channel)
        # Server Wrapping
        self.assert_not_wrapped(grpc.server)

    def assert_not_module_double_patched(self, grpc):
        # Client Wrapping
        self.assert_not_double_wrapped(grpc.insecure_channel)
        self.assert_not_double_wrapped(grpc.secure_channel)
        self.assert_not_double_wrapped(grpc.intercept_channel)
        # Server Wrapping
        self.assert_not_double_wrapped(grpc.server)
