"""
Class based views used for Django tests.
"""

from functools import partial
import hashlib

from django.contrib.auth.models import User
from django.contrib.syndication.views import Feed
from django.http import Http404
from django.http import HttpResponse
from django.template import loader
from django.template.response import TemplateResponse
from django.utils.safestring import mark_safe
from django.views.generic import ListView
from django.views.generic import TemplateView
from django.views.generic import View

from ddtrace import tracer
from ddtrace.contrib.trace_utils import set_user


class UserList(ListView):
    model = User
    template_name = "users_list.html"


class TemplateCachedUserList(ListView):
    model = User
    template_name = "cached_list.html"


class SafeTemplateUserList(ListView):
    model = User
    template_name = mark_safe("cached_list.html")


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
    response = HttpResponse("Hello, test app.")
    response["my-response-header"] = "my_response_value"
    return response


def alter_resource(request):
    root = tracer.current_root_span()
    root.resource = "custom django.request resource"

    return HttpResponse("")


def template_view(request):
    """
    View that uses a template instance
    """
    template = loader.select_template(["basic.html"])
    return TemplateResponse(request, template)


def template_simple_view(request):
    """
    Basic django templated view
    """
    return TemplateResponse(request, "basic.html")


def template_list_view(request):
    """
    For testing resolving a list of templates
    """
    return TemplateResponse(request, ["doesntexist.html", "basic.html"])


class CustomDispatchMixin(View):
    def dispatch(self, request):
        self.dispatch_call_counter += 1
        return super(CustomDispatchMixin, self).dispatch(request)


class AnotherCustomDispatchMixin(View):
    def dispatch(self, request):
        self.dispatch_call_counter += 1
        return super(AnotherCustomDispatchMixin, self).dispatch(request)


class ComposedTemplateView(TemplateView, CustomDispatchMixin, AnotherCustomDispatchMixin):
    template_name = "custom_dispatch.html"
    dispatch_call_counter = 0

    def get_context_data(self, **kwargs):
        context = super(ComposedTemplateView, self).get_context_data(**kwargs)
        context["dispatch_call_counter"] = self.dispatch_call_counter
        return context


class CustomGetView(View):
    def get(self, request):
        return HttpResponse("custom get")


class ComposedGetView(CustomGetView, CustomDispatchMixin):
    dispatch_call_counter = 0

    def get(self, request):
        if self.dispatch_call_counter == 1:
            return super(ComposedGetView, self).get(request)
        raise Exception("Custom dispatch not called.")


DISPATCH_CALLED = False


class CustomDispatchView(View):
    def dispatch(self, request):
        global DISPATCH_CALLED
        DISPATCH_CALLED = True
        return super(CustomDispatchView, self).dispatch(request)


class ComposedView(TemplateView, CustomDispatchView):
    template_name = "custom_dispatch.html"


def not_found_view(request):
    raise Http404("DNE")


def path_params_view(request, year, month):
    return HttpResponse(status=200)


def identify(request):
    set_user(
        tracer,
        user_id="usr.id",
        email="usr.email",
        name="usr.name",
        session_id="usr.session_id",
        role="usr.role",
        scope="usr.scope",
    )
    return HttpResponse(status=200)


def body_view(request):
    # Django >= 3
    if hasattr(request, "headers"):
        content_type = request.headers["Content-Type"]
    else:
        # Django < 3
        content_type = request.META["CONTENT_TYPE"]
    if content_type in ("application/json", "application/xml", "text/xml"):
        data = request.body
        return HttpResponse(data, status=200)
    else:
        data = request.POST
        return HttpResponse(str(dict(data)), status=200)


def weak_hash_view(request):
    m = hashlib.md5()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    return HttpResponse("OK", status=200)
