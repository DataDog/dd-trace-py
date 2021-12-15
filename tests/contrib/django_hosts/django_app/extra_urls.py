import django
from django.http import HttpResponse


if django.VERSION < (2, 0, 0):
    from django.conf.urls import url
else:
    from django.urls import re_path as url


def include_view(request):
    return HttpResponse(status=200)


urlpatterns = [
    url("test/", include_view),
]
