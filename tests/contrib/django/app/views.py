"""
Class based views used for Django tests.
"""
from django.conf.urls import url
from django.views.generic import ListView
from django.contrib.auth.models import User


class UserList(ListView):
    model = User
    template_name = 'users_list.html'


# use this url patterns for tests
urlpatterns = [
    url(r'^users/$', UserList.as_view(), name='users-list')
]
