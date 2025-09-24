import asyncio

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter
from channels.routing import URLRouter
from django.core.asgi import get_asgi_application
from django.http import StreamingHttpResponse
from django.urls import re_path

from ddtrace.contrib.internal.asgi.middleware import TraceMiddleware


application = get_asgi_application()


async def simple_asgi_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"Content-Type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"Hello World. It's me simple asgi app"})


async def generate_buffer():
    await asyncio.sleep(0.1)
    return ["buffer1", "buffer2", "buffer3"]


async def stream_view(_request):
    response = await generate_buffer()

    async def streaming_content_generator():
        async for chunk in response:
            yield chunk

    return StreamingHttpResponse(
        streaming_content=streaming_content_generator(),
        content_type="text/event-stream",
    )


channels_application = ProtocolTypeRouter(
    {
        "http": AuthMiddlewareStack(
            URLRouter(
                [
                    re_path(r"traced-simple-asgi-app/", TraceMiddleware(simple_asgi_app)),
                    re_path(r"simple-asgi-app/", simple_asgi_app),
                    re_path(r"stream/", stream_view),
                    re_path(r"", application),
                ]
            ),
        )
    }
)
