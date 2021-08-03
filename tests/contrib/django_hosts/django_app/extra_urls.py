from django.conf.urls import url
from django.http import HttpResponse


def include_view(request):
    return HttpResponse(status=200)


urlpatterns = [
    url("test/", include_view),
]
