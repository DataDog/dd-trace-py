import django
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.urls import include
from django.urls import path
from django.urls import re_path
from django.views.decorators.cache import cache_page
from django.views.generic import TemplateView

from ddtrace import tracer

from .. import views


# django.conf.urls.url was deprecated in django 3 and removed in django 4
if django.VERSION < (4, 0, 0):
    from django.conf.urls import url as handler
else:
    from django.urls import re_path as handler


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


def shutdown(request):
    # Endpoint used to flush traces to the agent when doing snapshots.
    tracer.shutdown()
    return HttpResponse(status=200)


urlpatterns = [
    handler(r"^$", views.index),
    handler(r"^simple/$", views.BasicView.as_view()),
    handler(r"^users/$", views.UserList.as_view(), name="users-list"),
    handler(r"^cached-template/$", views.TemplateCachedUserList.as_view(), name="cached-template-list"),
    handler(r"^safe-template/$", views.SafeTemplateUserList.as_view(), name="safe-template-list"),
    handler(r"^cached-users/$", cache_page(60)(views.UserList.as_view()), name="cached-users-list"),
    handler(r"^fail-view/$", views.ForbiddenView.as_view(), name="forbidden-view"),
    handler(r"^authenticated/$", authenticated_view, name="authenticated-view"),
    handler(r"^static-method-view/$", views.StaticMethodView.as_view(), name="static-method-view"),
    handler(r"^fn-view/$", views.function_view, name="fn-view"),
    handler(r"^feed-view/$", views.FeedView(), name="feed-view"),
    handler(r"^partial-view/$", views.partial_view, name="partial-view"),
    handler(r"^lambda-view/$", views.lambda_view, name="lambda-view"),
    handler(r"^error-500/$", views.error_500, name="error-500"),
    handler(r"^template-view/$", views.template_view, name="template-view"),
    handler(r"^template-simple-view/$", views.template_simple_view, name="template-simple-view"),
    handler(r"^template-list-view/$", views.template_list_view, name="template-list-view"),
    path("path-params/<int:year>/<str:month>/", views.path_params_view, name="path-params-view"),
    re_path(r"re-path.*/", repath_view),
    path("path/", path_view),
    path("include/", include("tests.contrib.django.django_app.extra_urls")),
    # This must precede composed-view.
    handler(r"^some-static-view/$", TemplateView.as_view(template_name="my-template.html")),
    handler(r"^composed-template-view/$", views.ComposedTemplateView.as_view(), name="composed-template-view"),
    handler(r"^composed-get-view/$", views.ComposedGetView.as_view(), name="composed-get-view"),
    handler(r"^composed-view/$", views.ComposedView.as_view(), name="composed-view"),
    handler(r"^404-view/$", views.not_found_view, name="404-view"),
    handler(r"^shutdown-tracer/$", shutdown, name="shutdown-tracer"),
    handler(r"^alter-resource/$", views.alter_resource),
    handler(r"^identify/$", views.identify, name="identify"),
    handler(r"^body/$", views.body_view, name="body_view"),
]
