from ddtrace.contrib.django.legacy import patch
patch()

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
