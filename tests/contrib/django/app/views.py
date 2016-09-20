"""
Class based views used for Django tests.
"""
from django.http import HttpResponse
from django.conf.urls import url
from django.views.generic import ListView, TemplateView
from django.contrib.auth.models import User


class UserList(ListView):
    model = User
    template_name = 'users_list.html'


class ForbiddenView(TemplateView):
    def get(self, request, *args, **kwargs):
        return HttpResponse(status=403)


# use this url patterns for tests
urlpatterns = [
    url(r'^users/$', UserList.as_view(), name='users-list'),
    url(r'^fail-view/$', ForbiddenView.as_view(), name='forbidden-view'),
]
