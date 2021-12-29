from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter
from channels.routing import URLRouter
from django.core.asgi import get_asgi_application
from django.urls import re_path

from ddtrace.contrib.asgi import TraceMiddleware


application = get_asgi_application()

channels_application = ProtocolTypeRouter(
    {
        "http": AuthMiddlewareStack(
            URLRouter(
                [
                    re_path(r"", application),
                    re_path(r"^shutdown-tracer/$", application),
                    re_path(r"^error-500/$", application),
                ]
            ),
        )
    }
)


async def simple_asgi_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"Content-Type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"Hello World"})


simple_application = ProtocolTypeRouter(
    {
        "http": AuthMiddlewareStack(
            URLRouter(
                [re_path(r"", TraceMiddleware(simple_asgi_app))],
            )
        )
    }
)
