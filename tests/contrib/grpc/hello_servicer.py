import grpc

from .hello_pb2 import HelloReply
from .hello_pb2_grpc import HelloServicer


class _HelloServicer(HelloServicer):
    def SayHello(self, request, context):
        if request.name == "propogator":
            metadata = context.invocation_metadata()
            context.set_code(grpc.StatusCode.OK)
            message = ";".join(w.key + "=" + w.value for w in metadata if w.key.startswith("x-datadog"))
            return HelloReply(message=message)

        if request.name == "abort":
            context.abort(grpc.StatusCode.ABORTED, "aborted")

        if request.name == "exception":
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "exception")

        return HelloReply(message="Hello {}".format(request.name))

    def SayHelloTwice(self, request, context):
        yield HelloReply(message="first response")

        if request.name == "exception":
            context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "exception")

        if request.name == "once":
            # Mimic behavior of scenario where only one result is expected from
            # streaming response and the RPC is successfully terminated, as is
            # the case with grpc_helpers._StreamingResponseIterator in the
            # Google API Library wraps a _MultiThreadedRendezvous future. An
            # example of this iterator only called once is in the Google Cloud
            # Firestore library.
            # https://github.com/googleapis/python-api-core/blob/f87bccbfda11d2c2d1a2ddb6611c1209e29289d9/google/api_core/grpc_helpers.py#L80-L116
            # https://github.com/googleapis/python-firestore/blob/e57258c51e4b4aa664cc927454056412756fc7ac/google/cloud/firestore_v1/document.py#L400-L404
            return

        yield HelloReply(message="secondresponse")

    def SayHelloLast(self, request_iterator, context):
        names = [r.name for r in list(request_iterator)]

        if "exception" in names:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "exception")

        return HelloReply(message="{}".format(";".join(names)))

    def SayHelloRepeatedly(self, request_iterator, context):
        last_request = None
        for request in request_iterator:
            if last_request is not None:
                yield HelloReply(message="{}".format(";".join([last_request.name, request.name])))
                last_request = None
            else:
                last_request = request

        # response for dangling request
        if last_request is not None:
            yield HelloReply(message="{}".format(last_request.name))

    def SayHelloUnknown(self, request, context):
        yield HelloReply(message="unknown")
