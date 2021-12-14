from django.urls import re_path
from django.views.decorators.cache import cache_page
from django.views.generic import TemplateView

from .. import views


urlpatterns = [
    re_path(r"^$", views.index),
    re_path(r"^simple/$", views.BasicView.as_view()),
    re_path(r"^users/$", views.UserList.as_view(), name="users-list"),
    re_path(r"^cached-template/$", views.TemplateCachedUserList.as_view(), name="cached-template-list"),
    re_path(r"^safe-template/$", views.SafeTemplateUserList.as_view(), name="safe-template-list"),
    re_path(r"^cached-users/$", cache_page(60)(views.UserList.as_view()), name="cached-users-list"),
    re_path(r"^fail-view/$", views.ForbiddenView.as_view(), name="forbidden-view"),
    re_path(r"^static-method-view/$", views.StaticMethodView.as_view(), name="static-method-view"),
    re_path(r"^fn-view/$", views.function_view, name="fn-view"),
    re_path(r"^feed-view/$", views.FeedView(), name="feed-view"),
    re_path(r"^partial-view/$", views.partial_view, name="partial-view"),
    re_path(r"^lambda-view/$", views.lambda_view, name="lambda-view"),
    re_path(r"^error-500/$", views.error_500, name="error-500"),
    re_path(r"^template-view/$", views.template_view, name="template-view"),
    re_path(r"^template-simple-view/$", views.template_simple_view, name="template-simple-view"),
    re_path(r"^template-list-view/$", views.template_list_view, name="template-list-view"),
    # This must precede composed tests.
    re_path(r"some-static-view/", TemplateView.as_view(template_name="my-template.html")),
    re_path(r"^composed-template-view/$", views.ComposedTemplateView.as_view(), name="composed-template-view"),
    re_path(r"^composed-get-view/$", views.ComposedGetView.as_view(), name="composed-get-view"),
    re_path(r"^composed-view/$", views.ComposedView.as_view(), name="composed-view"),
    re_path(r"^alter-resource/$", views.alter_resource, name="alter-resource"),
]
