from django.urls import re_path
from django.http import HttpResponse


def include_view(request):
    return HttpResponse(status=200)


urlpatterns = [
    re_path("test/", include_view),
]
