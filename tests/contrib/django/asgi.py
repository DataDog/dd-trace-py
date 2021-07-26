from django.core.asgi import get_asgi_application

from ddtrace.contrib.asgi import TraceMiddleware


application = TraceMiddleware(get_asgi_application())
