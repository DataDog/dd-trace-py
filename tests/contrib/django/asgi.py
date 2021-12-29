import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter
from channels.routing import URLRouter
from django.core.asgi import get_asgi_application
from django.urls import re_path


# Application must be run in the project root to find this settings ex. ddtrace/
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.contrib.django.django_app.settings")


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
