"""
This module provides mixins for Django class-based views and Django
Rest Framework viewsets that ensure request spans are correctly
attributed to the method responsible for handling them.
"""

from .middleware import _get_req_span


class ViewTraceMixin(object):
    """
    A mixin for views inheriting from :class:`django.views.generic.View`
    that correctly attributes request spans to the class-based view
    method that handled them.
    """

    def dispatch(self, request, *args, **kwargs):
        """
        Ensure that a request span is correctly attributed to the method
        that handled it.

        :param request: the request from the client.
        :param args: positional request arguments.
        :param kwargs: keyword request arguments.
        """
        span = _get_req_span()
        if span is not None:
            method = request.method.lower()
            handler = getattr(self, method, None)
            if method in self.http_method_names and handler is not None:
                module = self.__module__
                view_name = self.__class__.__name__
                action = handler.__name__
                span.resource = "{}.{}.{}".format(module, view_name, action)
        return super(ViewTraceMixin, self).dispatch(request, *args, **kwargs)


class ViewSetTraceMixin(object):
    """
    A mixin for views with :class:`rest_framework.viewsets.ViewSetMixin`
    in their inheritance hierarchy that correctly attributes request
    spans to the method that handled them.
    """

    def initialize_request(self, request, *args, **kwargs):
        """
        Ensure that the resource name of a span correctly attributes the
        request to the method that handled it.

        :param request: the request from the client.
        :param args: positional request arguments.
        :param kwargs: keyword request arguments.
        """
        span = _get_req_span()
        if span is not None:
            module = self.__module__
            view_name = self.__class__.__name__
            action = self.action
            span.resource = "{}.{}.{}".format(module, view_name, action)
        return super(ViewSetTraceMixin, self).initialize_request(request, *args, **kwargs)
