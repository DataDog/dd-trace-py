from django.conf.urls import url
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.views.decorators.cache import cache_page
from django.urls import include, path, re_path

from .. import views


def repath_view(request):
    return HttpResponse(status=200)


def path_view(request):
    return HttpResponse(status=200)


def authenticated_view(request):
    """
    This view can be used to test requests with an authenticated user. Create a
    user with a default username, save it and then use this user to log in.
    Always returns a 200.
    """

    user = User(username="Jane Doe")
    user.save()
    login(request, user)
    return HttpResponse(status=200)


urlpatterns = [
    url(r"^$", views.index),
    url(r"^simple/$", views.BasicView.as_view()),
    url(r"^users/$", views.UserList.as_view(), name="users-list"),
    url(r"^cached-template/$", views.TemplateCachedUserList.as_view(), name="cached-template-list"),
    url(r"^cached-users/$", cache_page(60)(views.UserList.as_view()), name="cached-users-list"),
    url(r"^fail-view/$", views.ForbiddenView.as_view(), name="forbidden-view"),
    url(r"^authenticated/$", authenticated_view, name="authenticated-view"),
    url(r"^static-method-view/$", views.StaticMethodView.as_view(), name="static-method-view"),
    url(r"^fn-view/$", views.function_view, name="fn-view"),
    url(r"^feed-view/$", views.FeedView(), name="feed-view"),
    url(r"^partial-view/$", views.partial_view, name="partial-view"),
    url(r"^lambda-view/$", views.lambda_view, name="lambda-view"),
    url(r"^error-500/$", views.error_500, name="error-500"),
    url(r"^template-view/$", views.template_view, name="template-view"),
    url(r"^template-simple-view/$", views.template_simple_view, name="template-simple-view"),
    url(r"^template-list-view/$", views.template_list_view, name="template-list-view"),
    re_path(r"re-path.*/", repath_view),
    path("path/", path_view),
    path("include/", include("tests.contrib.django.django_app.extra_urls")),
]
