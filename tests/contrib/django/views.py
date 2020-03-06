"""
Class based views used for Django tests.
"""

from functools import partial

from django.http import HttpResponse

from django.views.generic import ListView, TemplateView, View

from django.contrib.auth.models import User
from django.contrib.syndication.views import Feed


class UserList(ListView):
    model = User
    template_name = "users_list.html"


class TemplateCachedUserList(ListView):
    model = User
    template_name = "cached_list.html"


class BasicView(View):
    def get(self, request):
        return HttpResponse("")

    def post(self, request):
        return HttpResponse("")

    def delete(self, request):
        return HttpResponse("")

    def head(self, request):
        return HttpResponse("")


class ForbiddenView(TemplateView):
    def get(self, request, *args, **kwargs):
        return HttpResponse(status=403)


class StaticMethodView(View):
    @staticmethod
    def get(request):
        return HttpResponse("")


def function_view(request):
    return HttpResponse(status=200)


def error_500(request):
    raise Exception("Error 500")


class FeedView(Feed):
    """
    A callable view that is part of the Django framework
    """

    title = "Police beat site news"
    link = "/sitenews/"
    description = "Updates on changes and additions to police beat central."

    def items(self):
        return []

    def item_title(self, item):
        return "empty"

    def item_description(self, item):
        return "empty"


partial_view = partial(function_view)

# disabling flake8 test below, yes, declaring a func like this is bad, we know
lambda_view = lambda request: function_view(request)  # NOQA


def index(request):
    return HttpResponse("Hello, test app.")
