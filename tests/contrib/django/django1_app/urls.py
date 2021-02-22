from django.conf.urls import url
from django.views.decorators.cache import cache_page
from django.views.generic import TemplateView

from .. import views


urlpatterns = [
    url(r"^$", views.index),
    url(r"^simple/$", views.BasicView.as_view()),
    url(r"^users/$", views.UserList.as_view(), name="users-list"),
    url(r"^cached-template/$", views.TemplateCachedUserList.as_view(), name="cached-template-list"),
    url(r"^safe-template/$", views.SafeTemplateUserList.as_view(), name="safe-template-list"),
    url(r"^cached-users/$", cache_page(60)(views.UserList.as_view()), name="cached-users-list"),
    url(r"^fail-view/$", views.ForbiddenView.as_view(), name="forbidden-view"),
    url(r"^static-method-view/$", views.StaticMethodView.as_view(), name="static-method-view"),
    url(r"^fn-view/$", views.function_view, name="fn-view"),
    url(r"^feed-view/$", views.FeedView(), name="feed-view"),
    url(r"^partial-view/$", views.partial_view, name="partial-view"),
    url(r"^lambda-view/$", views.lambda_view, name="lambda-view"),
    url(r"^error-500/$", views.error_500, name="error-500"),
    url(r"^template-view/$", views.template_view, name="template-view"),
    url(r"^template-simple-view/$", views.template_simple_view, name="template-simple-view"),
    url(r"^template-list-view/$", views.template_list_view, name="template-list-view"),
    # This must precede composed tests.
    url(r"some-static-view/", TemplateView.as_view(template_name="my-template.html")),
    url(r"^composed-template-view/$", views.ComposedTemplateView.as_view(), name="composed-template-view"),
    url(r"^composed-get-view/$", views.ComposedGetView.as_view(), name="composed-get-view"),
    url(r"^composed-view/$", views.ComposedView.as_view(), name="composed-view"),
]
