"""WSGI entry point for the Django test application.

This enables running the Django test app under Gunicorn using the
``tests.appsec.integrations.django_tests.django_app.wsgi:application`` target.
"""

import ddtrace.auto  # noqa: F401  # isort: skip

from django.core.wsgi import get_wsgi_application

from ddtrace.internal.settings import env


env.setdefault("DJANGO_SETTINGS_MODULE", "tests.appsec.integrations.django_tests.django_app.settings")

application = get_wsgi_application()
