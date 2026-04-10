import time

from fastapi import FastAPI
import grpc
import pytest
import ray
from ray import serve
from ray.serve.config import gRPCOptions
from ray.serve.generated import serve_pb2
from ray.serve.generated import serve_pb2_grpc
from ray.serve.handle import DeploymentHandle
import requests
from starlette.requests import Request

from ddtrace import tracer
from ddtrace.internal.utils.version import parse_version


RAY_SNAPSHOT_IGNORES = [
    "meta.tracestate",
    "meta.ray.serve.handle_id",
    "meta.ray.serve.request_id",
    "meta.ray.serve.replica_id",
    "meta.ray.serve.deployment_id",
    "meta.ray.serve.handle_source",
    "meta.error.message",
    "meta.error.stack",
]

RAY_SERVE_SNAPSHOT_VARIANTS = {
    "ray_2_46": parse_version(ray.__version__) < (2, 47),
    "ray_2_46_plus": parse_version(ray.__version__) >= (2, 47),
}


@pytest.fixture(scope="session", autouse=True)
def ray_runtime():
    ray.init(
        ignore_reinit_error=True,
        _tracing_startup_hook="ddtrace.contrib.ray:setup_tracing",
    )
    yield
    ray.shutdown()


class TestRawServeApp:
    @pytest.fixture(scope="class")
    def raw_serve_app(self):
        @serve.deployment
        class ClassDeployment:
            def __call__(self, path: str = "/") -> str:
                if path == "/error":
                    raise ValueError("ClassDeployment forced error for /error path")
                return "Class"

        @serve.deployment
        def FunctionDeployment() -> str:
            return "Function"

        class Ingress:
            def __init__(
                self,
                class_handle: DeploymentHandle,
                func_handle: DeploymentHandle,
            ):
                self._class_handle = class_handle
                self._func_handle = func_handle

            async def __call__(self, request: Request) -> str:
                class_response = self._class_handle.remote(request.url.path)
                func_response = self._func_handle.remote()
                return (await class_response) + (await func_response)

        app = serve.deployment(Ingress).bind(  # type: ignore[attr-defined]
            ClassDeployment.bind(),  # type: ignore[attr-defined]
            FunctionDeployment.bind(),  # type: ignore[attr-defined]
        )
        serve.run(app)

        base_url = "http://127.0.0.1:8000"
        yield base_url

        serve.shutdown()

    @pytest.mark.snapshot(ignores=RAY_SNAPSHOT_IGNORES)
    @pytest.mark.no_getattr_patch()
    def test_ray_deployment_interaction(self, raw_serve_app):
        resp = requests.get(f"{raw_serve_app}/", timeout=2)
        assert resp.status_code == 200
        assert resp.text == "ClassFunction"
        time.sleep(5)

    @pytest.mark.snapshot(ignores=RAY_SNAPSHOT_IGNORES)
    @pytest.mark.no_getattr_patch()
    def test_raw_serve_error_path_without_reloading(self, raw_serve_app):
        error_resp = requests.get(f"{raw_serve_app}/error", timeout=2)
        assert error_resp.status_code == 500
        assert error_resp.text == "Internal Server Error"

        time.sleep(5)


class TestFastAPIServeApp:
    @pytest.fixture(scope="class")
    def fastapi_serve_app(self):
        app = FastAPI()

        @serve.deployment
        class Hello:
            def __call__(self, name: str) -> str:
                return f"Hello {name}"

        @serve.deployment
        @serve.ingress(app)
        class Ingress:
            def __init__(self, hello_handle: DeploymentHandle):
                self._hello_handle = hello_handle

            @app.get("/name/{name}")
            async def root(self, name: str) -> str:
                hello_response = self._hello_handle.remote(name)
                return await hello_response

        serve_app = Ingress.bind(Hello.bind())  # type: ignore[attr-defined]
        serve.run(serve_app)

        base_url = "http://127.0.0.1:8000"
        yield base_url

        serve.shutdown()

    @pytest.mark.snapshot(ignores=RAY_SNAPSHOT_IGNORES, variants=RAY_SERVE_SNAPSHOT_VARIANTS)
    def test_fastapi_name_path(self, fastapi_serve_app):
        """This test needs a variant because resource name for proxy_request span
        is different between ray versions
        """

        resp = requests.get(f"{fastapi_serve_app}/name/foo", timeout=2)
        assert resp.status_code == 200
        assert resp.json() == "Hello foo"

        time.sleep(5)


class TestGrpcServeApp:
    @pytest.fixture(scope="class")
    def grpc_serve_app(self):
        @serve.deployment
        class TraceIDDeployment:
            def __call__(self, request):
                response = serve_pb2.UserDefinedResponse()
                response.greeting = "constant-response"  # type: ignore[attr-defined]
                return response

        serve.start(
            grpc_options=gRPCOptions(
                grpc_servicer_functions=[
                    "ray.serve.generated.serve_pb2_grpc.add_UserDefinedServiceServicer_to_server",
                ],
            )
        )
        serve.run(TraceIDDeployment.bind())  # type: ignore[attr-defined]

        yield "127.0.0.1:9000"

        serve.shutdown()

    @pytest.mark.snapshot(ignores=RAY_SNAPSHOT_IGNORES)
    def test_grpc_context_propagation(self, grpc_serve_app):
        with tracer.trace("test.grpc_parent") as _:
            channel = grpc.insecure_channel(grpc_serve_app)
            stub = serve_pb2_grpc.UserDefinedServiceStub(channel)
            request = serve_pb2.UserDefinedMessage()
            request.name = "foo"  # type: ignore[attr-defined]
            response = stub.__call__(request, timeout=2)

        assert response.greeting == "constant-response"
        time.sleep(5)
