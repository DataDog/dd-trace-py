def fn_middleware(get_response):
    """Function factory middleware."""
    def mw(request):
        response = get_response(request)
        return response
    return mw


class ClsMiddleware:
    """Class middleware."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return response


class EverythingMiddleware:
    """Middleware using all possible middleware hooks."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return response

    def process_response(self, req, resp):
        return resp

    def process_request(self, req):
        return req

    def process_exception(self, request, exception):
        pass

    def process_view(self, request, view_func, view_args, view_kwargs):
        pass

    def process_template_response(self, req, resp):
        return resp
