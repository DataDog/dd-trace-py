import django
from django.http import HttpResponse


if django.VERSION < (4, 0, 0):
    from django.conf.urls import url as handler
else:
    from django.urls import re_path as handler


def include_view(request):
    return HttpResponse(status=200)


urlpatterns = [
    handler("test/", include_view),
]
