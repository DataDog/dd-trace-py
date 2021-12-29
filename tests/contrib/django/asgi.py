from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter
from channels.routing import URLRouter
from django.core.asgi import get_asgi_application
from django.urls import re_path


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
